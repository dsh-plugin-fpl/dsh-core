/** Independent imported configuration copies owned by this DSH profile. */
import type { Context } from "@deepseek-ai/cordis";
import { credentialRef } from "@deepseek-ai/dsh-credentials";
import { defineTool } from "@deepseek-ai/dsh-tools";
import { randomBytes, createCipheriv, createDecipheriv } from "node:crypto";
import { mkdir, readFile, writeFile, rename, rm } from "node:fs/promises";
import { resolve } from "node:path";
import { installSkill } from "./archives.js";
import {
  credentialFields,
  type PortableIntegration,
  type IntegrationKind,
  type ImportOptions,
  type IntegrationAdapter,
} from "./types.js";
import { validateConfiguration } from "./validation.js";
import Ajv2020 from "ajv/dist/2020.js";

type Copy = {
  item: PortableIntegration;
  version: number;
  enabled: boolean;
  error?: string;
  skillRoot?: string;
};
type State = { records: Record<string, Copy>; receipts: Record<string, any> };
export class ManagedIntegrations {
  private root: string;
  private state: State = { records: {}, receipts: {} };
  private key?: Buffer;
  private ready: Promise<void>;
  private queue: Promise<unknown> = Promise.resolve();
  private active = new Map<string, () => unknown>();
  private disposed = false;
  private activating = new Map<string, Promise<void>>();
  constructor(private ctx: Context) {
    const profile = ctx.get("profileContext") as any;
    this.root = resolve(profile.dir, ".runtime/fpl-core-integrations");
    this.ready = this.load();
    this.ready.catch(() => {});
    ctx.effect(() => async () => {
      this.disposed = true;
      await this.ready.catch(() => {});
      for (const dispose of this.active.values()) await dispose();
      this.active.clear();
    });
    ctx.inject(["tools"], () => {
      void this.restore().catch(() => {});
    });
    ctx.inject(["skills"], () => {
      void this.restore().catch(() => {});
    });
  }
  private async load() {
    await mkdir(this.root, { recursive: true, mode: 0o700 });
    const ref = credentialRef("DSH_CORE_INTEGRATIONS_KEY");
    let key = (await this.ctx.credentials.resolve(ref))?.value;
    if (!key) {
      key = randomBytes(32).toString("base64");
      await this.ctx.credentials.set(ref, key);
    }
    this.key = Buffer.from(key, "base64");
    if (this.key.length !== 32) throw new Error("DSH 集成密钥格式无效");
    try {
      const raw = await readFile(resolve(this.root, "catalog.enc"));
      const decipher = createDecipheriv(
        "aes-256-gcm",
        this.key,
        raw.subarray(0, 12),
      );
      decipher.setAuthTag(raw.subarray(12, 28));
      this.state = JSON.parse(
        Buffer.concat([
          decipher.update(raw.subarray(28)),
          decipher.final(),
        ]).toString(),
      );
    } catch (error: any) {
      if (error.code !== "ENOENT")
        throw new Error("DSH 集成配置无法解密，请保留原凭据");
    }
    await this.restore();
  }
  private async persist() {
    const iv = randomBytes(12),
      cipher = createCipheriv("aes-256-gcm", this.key!, iv);
    const body = Buffer.concat([
      cipher.update(JSON.stringify(this.state)),
      cipher.final(),
    ]);
    const file = resolve(this.root, "catalog.enc"),
      temporary = file + "." + randomBytes(6).toString("hex");
    await writeFile(temporary, Buffer.concat([iv, cipher.getAuthTag(), body]), {
      mode: 0o600,
      flag: "wx",
    });
    await rename(temporary, file);
  }
  private serialized<T>(fn: () => Promise<T>): Promise<T> {
    const run = this.queue.then(fn, fn);
    this.queue = run.catch(() => {});
    return run;
  }
  private async restore() {
    if (!this.key || this.disposed) return;
    for (const [id, row] of Object.entries(this.state.records))
      if (row.enabled && !this.active.has(id)) {
        try {
          await this.activate(id, row);
        } catch {
          row.error = "启用失败，请检查服务和运行环境";
        }
      }
  }
  async list() {
    await this.ready;
    return Object.entries(this.state.records).map(([id, row]) => ({
      id,
      kind: row.item.kind,
      name: row.item.name,
      revision: String(row.version),
      enabled: row.enabled,
      error: row.error || "",
      secret_fields: credentialFields(row.item.config),
      command:
        row.item.kind === "mcp" && row.item.config.transport === "stdio"
          ? row.item.config.command
          : undefined,
      arguments:
        row.item.kind === "mcp" && row.item.config.transport === "stdio"
          ? row.item.config.arguments
          : undefined,
    }));
  }
  async export(kind: IntegrationKind, id: string) {
    await this.ready;
    const row = this.state.records[id];
    if (!row || row.item.kind !== kind) throw new Error("导入副本不存在");
    return {
      ...structuredClone(row.item),
      id,
      revision: String(row.version),
      config: { ...row.item.config, enabled: false },
    };
  }
  async receipt(id: string) {
    await this.ready;
    return this.state.receipts[id] || { status: "not-found" };
  }
  async import(item: PortableIntegration, options: ImportOptions) {
    await this.ready;
    return this.serialized(async () => {
      item = validateConfiguration(item);
      const known = this.state.receipts[options.request_id];
      if (known?.status === "completed") return known.result;
      if (known?.status === "applying" || known?.status === "unknown")
        throw new Error("请先核对原导入回执");
      if (!/^[0-9a-f-]{36}$/.test(options.target_id))
        throw new Error("导入目标 ID 无效");
      const previous = this.state.records[options.target_id];
      if (options.create && previous) throw new Error("导入目标已存在");
      if (
        !options.create &&
        (!previous || String(previous.version) !== options.expected_revision)
      )
        throw new Error("目标副本已更新");
      if (previous && previous.item.kind !== item.kind)
        throw new Error("集成类型不匹配");
      const snapshot = structuredClone(this.state);
      let skillRoot: string | undefined;
      let committed = false;
      try {
        if (item.kind === "skills") {
          skillRoot = resolve(
            this.root,
            "skills",
            options.target_id,
            randomBytes(8).toString("hex"),
          );
          await installSkill(item.archive_base64 || "", skillRoot);
        }
        const version = (previous?.version ?? -1) + 1;
        const result = {
          id: options.target_id,
          kind: item.kind,
          name: item.name,
          revision: String(version),
        };
        this.state.records[options.target_id] = {
          item: {
            ...structuredClone(item),
            id: options.target_id,
            config: { ...structuredClone(item.config), enabled: false },
          },
          version,
          enabled: false,
          skillRoot,
        };
        this.state.receipts[options.request_id] = {
          status: "completed",
          result,
        };
        await this.persist();
        committed = true;
        const dispose = this.active.get(options.target_id);
        if (dispose) {
          try {
            await dispose();
          } catch {
            this.state.records[options.target_id].error =
              "旧实例退出未确认，请重启 DSH 后启用新配置";
            await this.persist();
          }
        }
        this.active.delete(options.target_id);
        return result;
      } catch (error) {
        if (committed) return this.state.receipts[options.request_id].result;
        this.state = snapshot;
        if (skillRoot) await rm(skillRoot, { recursive: true, force: true });
        throw error;
      }
    });
  }
  async enable(id: string, enabled: boolean) {
    await this.ready;
    return this.serialized(async () => {
      const row = this.state.records[id];
      if (!row) throw new Error("配置不存在");
      if (this.active.has(id)) {
        await this.active.get(id)!();
        this.active.delete(id);
      }
      row.enabled = false;
      row.error = "";
      if (enabled) {
        try {
          await this.activate(id, row);
          row.enabled = true;
        } catch {
          row.error = "启用失败，请检查服务、命令和鉴权";
          await this.persist();
          throw new Error(row.error);
        }
      }
      row.version++;
      await this.persist();
      return { enabled: row.enabled };
    });
  }
  private async activate(id: string, row: Copy) {
    const pending = this.activating.get(id);
    if (pending) return pending;
    const run = this.activateOnce(id, row);
    this.activating.set(id, run);
    try {
      await run;
    } finally {
      this.activating.delete(id);
    }
  }
  private async activateOnce(id: string, row: Copy) {
    if (this.disposed) return;
    const config = row.item.config;
    if (row.item.kind === "mcp") {
      if (!this.ctx.get("tools")) throw new Error("工具服务不可用");
      const module = await import("@deepseek-ai/dsh-mcp-client");
      const options: any = {
        transport: config.transport === "stdio" ? "stdio" : "streamable-http",
        serverName: "fpl_" + id.replaceAll("-", "").slice(0, 24),
        command: config.command || "",
        args: config.arguments || [],
        env: config.environment || {},
        cwd: config.cwd || "",
        url: config.url || "",
        headers: {
          ...config.headers,
          ...(config.bearer_token
            ? { Authorization: "Bearer " + config.bearer_token }
            : {}),
        },
        toolCallTimeoutMs: config.timeout_ms || 30000,
        failOnStartupError: true,
      };
      const fiber = this.ctx.plugin(module, options);
      try {
        await fiber;
      } catch (error) {
        await fiber.dispose();
        throw error;
      }
      this.active.set(id, () => fiber.dispose());
    } else if (row.item.kind === "skills") {
      const skills = this.ctx.get("skills") as any;
      if (!skills) throw new Error("Skill 服务不可用");
      const markdown = await readFile(
        resolve(row.skillRoot!, "SKILL.md"),
        "utf8",
      );
      const content = markdown.replace(/^---\s*\n[\s\S]*?\n---\s*\n/, "");
      this.active.set(
        id,
        skills.register({
          name: row.item.config.name || row.item.name,
          description: config.description || row.item.name,
          content,
          source: "runtime",
          provider: "fpl-core-imports",
          resourceBase: { kind: "directory", path: row.skillRoot },
        }),
      );
    } else if (row.item.kind === "tool-apis") {
      const tools = this.ctx.get("tools") as any;
      if (!tools) throw new Error("工具服务不可用");
      const validator = new (Ajv2020 as any)({ strict: false }).compile(
        config.parameters_schema || { type: "object" },
      );
      const render = (value: any, input: any): any =>
        typeof value === "string"
          ? /^\{\{\s*(\w+)\s*\}\}$/.test(value)
            ? structuredClone(input[value.match(/^\{\{\s*(\w+)\s*\}\}$/)![1]])
            : value.replace(/\{\{\s*(\w+)\s*\}\}/g, (_, name) => {
                if (!(name in input)) throw new Error("缺少参数");
                return String(input[name]);
              })
          : Array.isArray(value)
            ? value.map((v) => render(v, input))
            : value && typeof value === "object"
              ? Object.fromEntries(
                  Object.entries(value).map(([k, v]) => [k, render(v, input)]),
                )
              : value;
      const definition = defineTool({
        name: "fpl_api_" + id.replaceAll("-", "").slice(0, 24),
        description: config.description || row.item.name,
        parameters: { input: { type: "json", required: true } },
        output: {
          schema: { type: "json" },
          render: (_a: any, value: any) => [
            { type: "text", text: JSON.stringify(value) },
          ],
        },
        async execute(args: any, execution: any) {
          if (!validator(args.input)) throw new Error("输入不符合工具 Schema");
          const url = new URL(config.url);
          if (
            !["http:", "https:"].includes(url.protocol) ||
            url.username ||
            url.password
          )
            throw new Error("工具地址无效");
          for (const [k, v] of Object.entries(
            render(config.query_template || {}, args.input),
          ))
            url.searchParams.set(k, String(v));
          const headers: Record<string, string> = {
            "Content-Type": "application/json",
          };
          if (config.auth_kind === "bearer")
            headers.Authorization = "Bearer " + config.secret;
          if (config.auth_kind === "api_key")
            headers[config.auth_header_name] = config.secret;
          const response = await fetch(url, {
            method: config.method,
            headers,
            body: ["GET", "HEAD"].includes(config.method)
              ? undefined
              : JSON.stringify(render(config.body_template, args.input)),
            redirect: "manual",
            signal: AbortSignal.any([
              execution.signal || new AbortController().signal,
              AbortSignal.timeout(30000),
            ]),
          });
          if (!response.ok)
            throw new Error("远端工具返回 HTTP " + response.status);
          const reader = response.body?.getReader();
          let bytes = 0;
          const chunks: Uint8Array[] = [];
          if (reader)
            try {
              for (;;) {
                const { done, value } = await reader.read();
                if (done) break;
                bytes += value.byteLength;
                if (bytes > 1024 * 1024) throw new Error("工具响应过大");
                chunks.push(value);
              }
            } finally {
              await reader.cancel();
            }
          let text = Buffer.concat(chunks).toString();
          if (config.secret)
            text = text.replaceAll(config.secret, "[REDACTED]");
          return { text: config.sensitive_result ? "敏感结果已隐藏" : text };
        },
      });
      this.active.set(id, tools.register(definition));
    } else if (row.item.kind === "external-connections")
      this.active.set(id, () => {});
  }
  adapter(): IntegrationAdapter {
    return {
      id: "core-managed",
      application_id: "core",
      name: "DSH 导入副本",
      kinds: ["tool-apis", "mcp", "skills", "external-connections"],
      list: () => this.list(),
      export: (kind, id) => this.export(kind, id),
      import: (item, options) => this.import(item, options),
      receipt: (id) => this.receipt(id),
    };
  }
}
