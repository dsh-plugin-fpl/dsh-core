/** Local process control delegates to each installed service's public launcher. */
import { execFile } from "node:child_process";
import {
  access,
  appendFile,
  chmod,
  mkdir,
  readFile,
  realpath,
} from "node:fs/promises";
import { isAbsolute, join, sep } from "node:path";
import { promisify } from "node:util";

const execute = promisify(execFile);
const pending = new Map<string, Promise<void>>();
function serial(key: string, action: () => Promise<void>) {
  const next = (pending.get(key) || Promise.resolve())
    .catch(() => {})
    .then(action);
  pending.set(key, next);
  void next
    .finally(() => {
      if (pending.get(key) === next) pending.delete(key);
    })
    .catch(() => {});
  return next;
}
export interface LocalServiceOptions {
  id: string;
  endpoint: string;
  serviceDirectory?: string;
  installation: URL;
  manageService?: boolean;
}
interface Installation {
  version: number;
  id: string;
  repository: string;
  port: number;
}
export class LocalService {
  private starting?: Promise<void>;
  private stopping?: Promise<void>;
  private stopRequested = false;
  private attempted = false;
  private directory = "";
  private readonly endpoint: URL;
  private readonly key: string;
  constructor(private options: LocalServiceOptions) {
    this.endpoint = new URL(options.endpoint);
    if (!/^(core|dsh-[a-z][a-z0-9-]{0,79})$/.test(options.id))
      throw new Error("服务标识无效");
    if (
      this.endpoint.protocol !== "http:" ||
      this.endpoint.hostname !== "127.0.0.1" ||
      this.endpoint.username ||
      this.endpoint.password ||
      this.endpoint.search ||
      this.endpoint.hash ||
      this.endpoint.pathname !== "/"
    ) {
      if (options.manageService !== false)
        throw new Error(
          "自动启停仅支持本机 127.0.0.1 HTTP 服务；其他连接请关闭 manageService",
        );
    }
    this.key = options.id + ":" + this.endpoint.origin;
  }
  private async prepare() {
    let installation: Installation | undefined;
    if (!this.options.serviceDirectory) {
      try {
        installation = JSON.parse(
          await readFile(this.options.installation, "utf8"),
        );
      } catch {
        throw new Error(
          "未找到服务安装位置。请先安装独立服务，在插件参数 serviceDirectory 中填写其目录",
        );
      }
      if (installation?.version !== 1 || installation.id !== this.options.id)
        throw new Error("服务安装描述与当前插件不一致");
    }
    const selected = this.options.serviceDirectory || installation!.repository;
    if (
      !selected ||
      !isAbsolute(selected) ||
      selected.split(sep).includes(".local")
    )
      throw new Error("服务目录必须是正式安装的绝对路径，不能使用 .local");
    try {
      this.directory = await realpath(selected);
    } catch {
      throw new Error("服务目录不存在：" + selected);
    }
    if (this.directory.split(sep).includes(".local"))
      throw new Error("服务目录不能依赖 .local 临时目录");
    let pkg;
    try {
      pkg = JSON.parse(
        await readFile(join(this.directory, "package.json"), "utf8"),
      );
    } catch {
      throw new Error("服务目录不存在或缺少 package.json：" + this.directory);
    }
    const expected = this.options.id === "core" ? "dsh-core" : this.options.id;
    if (
      pkg.name !== "@dsh-plugin-fpl/" + expected &&
      !(expected === "dsh-core" && pkg.name === "fpl-core-service")
    )
      throw new Error("服务目录属于其他应用，拒绝启动或停止");
    const core = this.options.id === "core";
    const program = core
      ? ".runtime/worker-venv/bin/python"
      : "scripts/application.mjs";
    try {
      await access(join(this.directory, program));
    } catch {
      throw new Error(
        "服务运行环境未安装，请在服务目录运行 " +
          (core ? "pnpm setup:service" : "pnpm setup"),
      );
    }
    if (
      !core &&
      installation &&
      Number(this.endpoint.port) !== installation.port
    )
      throw new Error("服务端口与本机安装描述不一致，请检查 endpoint");
  }
  private async run(action: "start" | "stop") {
    const logDirectory = join(this.directory, ".runtime/plugin-lifecycle");
    await mkdir(logDirectory, { recursive: true, mode: 0o700 });
    const log = join(logDirectory, this.options.id + ".log");
    const core = this.options.id === "core";
    const executable = core
      ? join(this.directory, ".runtime/worker-venv/bin/python")
      : process.execPath;
    const args = core
      ? [
          "-m",
          "dsh_core_worker.cli",
          action,
          "--no-open",
          "--from-plugin",
          "--port",
          this.endpoint.port || "80",
        ]
      : [
          join(this.directory, "scripts/application.mjs"),
          action,
          "--from-plugin",
          "--port",
          this.endpoint.port || "80",
        ];
    try {
      const result = await execute(executable, args, {
        cwd: this.directory,
        timeout: 90000,
        maxBuffer: 128 * 1024,
        killSignal: "SIGTERM",
        env: {
          ...process.env,
          ELECTRON_RUN_AS_NODE: "1",
          PYTHONPATH: join(this.directory, "python"),
        },
      });
      await appendFile(
        log,
        `[${new Date().toISOString()}] ${action}\n${result.stdout}${result.stderr}\n`,
        { mode: 0o600 },
      );
      await chmod(log, 0o600);
    } catch (error: any) {
      if (action === "start" && error.killed) this.attempted = true;
      await appendFile(
        log,
        `[${new Date().toISOString()}] ${action} failed\n${error.stdout || ""}${error.stderr || ""}\n`,
        { mode: 0o600 },
      );
      await chmod(log, 0o600);
      throw new Error(
        `${this.options.id} ${action === "start" ? "启动" : "停止"}失败（${error.killed ? "超时" : error.code || "执行错误"}），请检查日志：${log}`,
      );
    }
  }
  start() {
    if (this.options.manageService === false) return Promise.resolve();
    return (this.starting ??= serial(this.key, async () => {
      if (this.stopRequested) return;
      await this.prepare();
      if (this.stopRequested) return;
      await this.run("start");
      this.attempted = true;
    }));
  }
  stop() {
    if (this.options.manageService === false) return Promise.resolve();
    this.stopRequested = true;
    return (this.stopping ??= serial(this.key, async () => {
      if (this.attempted) await this.run("stop");
    }).catch((error) => {
      this.stopping = undefined;
      throw error;
    }));
  }
}
