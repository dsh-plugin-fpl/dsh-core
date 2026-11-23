/** Native DSH discovery uses public settings/inventory/skill interfaces only. */
import type { Context } from "@deepseek-ai/cordis";
import { readPluginInventory } from "@deepseek-ai/dsh-host-plugin-inventory";
import { createHash } from "node:crypto";
import { exportSkill } from "./archives.js";
import {
  credentialFields,
  type IntegrationAdapter,
  type IntegrationKind,
  type PortableIntegration,
} from "./types.js";
const digest = (value: unknown) =>
  createHash("sha256").update(JSON.stringify(value)).digest("hex");
export function nativeAdapter(ctx: Context): IntegrationAdapter {
  async function mcpForms(_secrets: boolean) {
    // ConfigEditor is the public full-config reader. Settings forms expose volatile fields only.
    const editor = ctx.get("configEditor") as any;
    if (!editor || !ctx.get("loader")) return [];
    const inventory = await readPluginInventory(ctx);
    const entries = new Map(
      inventory.entries
        .filter((e) => e.moduleName === "@deepseek-ai/dsh-mcp-client")
        .map((e) => [String(e.entryId), e]),
    );
    return editor
      .configuration()
      .filter((row: any) => entries.has(String(row.entry.id)))
      .map((row: any) => {
        const entry = row.entry;
        const value = entry.fiber?.config ?? entry.options.config;
        if (!value || typeof value !== "object")
          throw new Error("MCP 配置尚未就绪，请等待插件启动后重试");
        return {
          ns: String(entry.id),
          value: structuredClone(value),
          revision: digest(value),
          enabled: entries.get(String(entry.id))!.enabled,
        };
      });
  }
  async function exportRecord(
    kind: IntegrationKind,
    id: string,
    signal: AbortSignal,
  ): Promise<PortableIntegration> {
    signal.throwIfAborted();
    if (kind === "mcp") {
      const form = (await mcpForms(true)).find((r: any) => r.ns === id);
      if (!form) throw new Error("MCP 配置不存在");
      const c = form.value;
      const config = {
        name: c.serverName || id,
        description: "DSH MCP 配置",
        transport: c.transport === "stdio" ? "stdio" : "streamable_http",
        command: c.command || "",
        arguments: c.args || [],
        environment: c.env || {},
        cwd: c.cwd || "",
        url: c.url || "",
        headers: c.headers || {},
        bearer_token: "",
        timeout_ms: c.toolCallTimeoutMs || 30000,
        tags: [],
        enabled: false,
      };
      return {
        schema_version: 1,
        id,
        kind,
        name: config.name,
        revision: digest({ revision: form.revision, config }),
        config,
        warnings: ["DSH 的重连和启动策略不属于连接参数，由目标端独立设置。"],
      };
    }
    if (kind === "skills") {
      const skills = ctx.get("skills") as any;
      const skill = await skills?.get(id, { signal });
      if (!skill) throw new Error("Skill 不存在");
      const archive_base64 = await exportSkill(skill);
      const config = {
        name: skill.name,
        description: skill.description,
        tags: [],
        enabled: false,
      };
      return {
        schema_version: 1,
        id,
        kind,
        name: skill.name,
        revision: digest({ config, archive_base64 }),
        config,
        archive_base64,
      };
    }
    throw new Error("原生适配器不支持此类型");
  }
  return {
    id: "native-dsh",
    application_id: "core",
    name: "现有 DSH MCP / Skill",
    kinds: ["mcp", "skills"],
    can_import: false,
    async list(signal) {
      const rows: any[] = [];
      for (const form of await mcpForms(false))
        rows.push({
          id: form.ns,
          kind: "mcp",
          name: form.value.serverName || form.ns,
          revision: String(form.revision),
          enabled: form.enabled,
          secret_fields: credentialFields({
            environment: form.value.env || {},
            headers: form.value.headers || {},
          }),
        });
      const skills = ctx.get("skills") as any;
      if (skills)
        for (const row of await skills.list({ signal }))
          rows.push({
            id: row.name,
            kind: "skills",
            name: row.name,
            revision: digest(row),
            enabled: true,
            secret_fields: [],
          });
      return rows;
    },
    export: exportRecord,
    async import() {
      throw new Error("原生配置只读，请导入到 DSH 导入副本");
    },
    async receipt() {
      return { status: "not-found" };
    },
  };
}
