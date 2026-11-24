/** Installation control is available before the independent business service exists. */
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { access, appendFile, lstat, mkdir, readFile, realpath, rename, rm } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { LocalService, type LocalServiceOptions } from "./lifecycle.js";
import { prepareUv, runProgram } from "./runtime-bootstrap.js";
import { formalDirectory, installationLock, privateJson, readPrivateJson, serviceManifestSchema,
  SetupError, unpackService, type ServiceManifest } from "./runtime-files.js";
export type { ServiceManifest } from "./runtime-files.js";
export interface ManagedServiceOptions extends LocalServiceOptions { manifest?: URL }
export type SetupPhase = "missing" | "installing" | "awaiting_authorization" | "ready" | "stopped" | "failed" | "external";
export interface RuntimeStatus {
  id: string; name: string; phase: SetupPhase; step: string; endpoint: string;
  installed: boolean; version?: string; error?: { code: string; message: string };
  ffmpeg: boolean; mode: "managed" | "external";
}
interface Record { directory: string; sha256: string; version: string; account?: string; python?: string }
interface Entry { options: ManagedServiceOptions; name: string; handle: ManagedService;
  phase?: SetupPhase; step: string; error?: RuntimeStatus["error"]; pending?: Promise<void>; controller?: AbortController }
export class ManagedService {
  private local?: LocalService;
  constructor(private manager: RuntimeManager, readonly id: string) {}
  async start() {
    const options = await this.manager.launchOptions(this.id);
    if (options.manageService === false) return;
    this.local ||= new LocalService(options);
    try { await this.local.start(); this.manager.started(this.id); }
    catch (e) { this.manager.failed(this.id, e); throw e; }
  }
  async stop(lockPython?: string) { await this.local?.stop(lockPython); this.local = undefined; this.manager.stopped(this.id); }
  async dispose() { await this.manager.unregister(this.id); }
}
export class RuntimeManager {
  readonly root: string;
  private entries = new Map<string, Entry>();
  private closed = false;
  constructor(root = process.env.DSH_FPL_RUNTIME_ROOT || join(homedir(), "Library/Application Support/DSH-FPL-Runtime/.runtime")) {
    this.root = formalDirectory(root);
  }
  register(options: ManagedServiceOptions) {
    if (this.closed) throw new SetupError("setup_disposed", "安装控制已关闭");
    if (!/^(core|dsh-[a-z][a-z0-9-]{0,79})$/.test(options.id)) throw new SetupError("service_id", "服务标识无效");
    if (this.entries.has(options.id)) throw new SetupError("service_conflict", "此服务已有安装控制");
    const handle = new ManagedService(this, options.id);
    this.entries.set(options.id, { options: { ...options }, handle, name: options.id, step: "等待检查" });
    return handle;
  }
  private entry(id: string) {
    const value = this.entries.get(id);
    if (!value) throw new SetupError("service_unregistered", "请先启用对应的工坊插件");
    return value;
  }
  private recordPath(id: string) { return join(this.root, "records", id + ".json"); }
  async record(id: string): Promise<Record | undefined> {
    try {
      const value = await readPrivateJson(this.recordPath(id));
      if (!/^[a-f0-9]{64}$/.test(value.sha256) || value.directory !== join(this.root, "services", id, value.sha256))
        throw new SetupError("installation_record", "服务安装登记无效");
      const info = await lstat(value.directory);
      if (!info.isDirectory() || info.isSymbolicLink() || await realpath(value.directory) !== value.directory)
        throw new SetupError("installation_record", "服务安装目录已被替换");
      return value;
    } catch (e: any) { if (e.code === "ENOENT") return undefined; throw e; }
  }
  private async manifest(id: string): Promise<ServiceManifest> {
    const entry = this.entry(id);
    if (!entry.options.manifest) throw new SetupError("payload_missing", "此安装包未携带独立服务，请更新插件或指定外部服务目录");
    const value = serviceManifestSchema.parse(JSON.parse(await readFile(entry.options.manifest, "utf8")));
    if (value.id !== id || value.package !== "@dsh-plugin-fpl/" + (id === "core" ? "dsh-core" : id))
      throw new SetupError("service_manifest", "服务载荷与当前插件不匹配");
    entry.name = value.name;
    return value;
  }
  /** Read only the public storage locator, never a provider database. */
  async dataRoot() {
    const locator = process.env.DSH_FPL_STORAGE_CONFIG || join(homedir(), "Library/Preferences/dsh-fpl-storage.json");
    try {
      const value = await readPrivateJson(locator);
      const root = formalDirectory(value.root);
      const marker = JSON.parse(await readFile(join(root, ".dsh-fpl-storage.json"), "utf8"));
      if (value.version !== 1 || marker.instance !== value.instance) throw new SetupError("storage_instance", "统一存储目录不匹配");
      return root;
    } catch (e: any) {
      // A configured but unavailable disk must not turn into an empty replacement library.
      if (e.code !== "ENOENT" || await access(locator).then(() => true, () => false)) throw e;
      return formalDirectory(process.env.DSH_FPL_DATA_ROOT || join(homedir(), "Library/Application Support/DSH-FPL"));
    }
  }
  async configuration(id: string) {
    const entry = this.entry(id), record = await this.record(id);
    if (!record || entry.options.serviceDirectory || entry.options.manageService === false) return undefined;
    const base = await this.dataRoot(), stateRoot = id === "core" ? join(base, "core") : join(base, "workshops", id);
    return { directory: record.directory, stateRoot, lockPython: record.python, credentialFile: id === "core" ? undefined : join(stateRoot, "plugin.json") };
  }
  async launchOptions(id: string): Promise<LocalServiceOptions> {
    const entry = this.entry(id);
    if (entry.options.manageService === false || entry.options.serviceDirectory) return entry.options;
    const current = await this.configuration(id);
    if (current) return { ...entry.options, serviceDirectory: current.directory, stateRoot: current.stateRoot, lockPython: current.lockPython };
    // Continue accepting old locally generated path descriptors without publishing new ones.
    try {
      const descriptor = JSON.parse(await readFile(entry.options.installation, "utf8"));
      if (descriptor.version === 1 && descriptor.id === id && descriptor.repository) return entry.options;
    } catch (e: any) { if (e.code !== "ENOENT") throw e; }
    throw new SetupError("setup_required", "请在 FPL 安装与连接设置中安装独立服务");
  }
  started(id: string) { const e = this.entry(id); e.phase = undefined; e.step = "服务已启动"; e.error = undefined; }
  stopped(id: string) { const e = this.entry(id); if (!e.pending) { e.phase = undefined; e.step = "服务已停止"; } }
  failed(id: string, error: any) {
    const entry = this.entry(id);
    if (error instanceof SetupError && error.code === "setup_required") {
      entry.phase = "missing"; entry.step = error.message; entry.error = undefined; return;
    }
    entry.phase = "failed"; entry.step = "操作失败，可重试";
    entry.error = { code: error instanceof SetupError ? error.code : "service_failed",
      message: error instanceof SetupError ? error.message : "服务操作失败，请检查安装目录中的私有日志" };
  }
  async status(): Promise<RuntimeStatus[]> {
    let ffmpeg = false;
    try { await runProgram("ffmpeg", ["-version"], process.cwd()); await runProgram("ffprobe", ["-version"], process.cwd()); ffmpeg = true; } catch {}
    return Promise.all([...this.entries].map(async ([id, e]) => {
      let record: Record | undefined, version: string | undefined;
      try { record = await this.record(id); version = (await this.manifest(id)).version; }
      catch (error: any) { if (error.code !== "ENOENT" && !(error instanceof SetupError && error.code === "payload_missing")) this.failed(id, error); }
      let ready = false;
      try { const r = await fetch(new URL("/ready", e.options.endpoint), { signal: AbortSignal.timeout(1000), redirect: "error" });
        const value = r.ok ? await r.json() : null;
        ready = value?.status === "ready" && (id === "core" ? !value.application || value.application === "dsh-core" : value.application === id);
      } catch {}
      const external = !!e.options.serviceDirectory || e.options.manageService === false;
      const phase = e.phase || (external ? "external" : !record ? "missing" : !ready ? "stopped" : !record.account ? "awaiting_authorization" : "ready");
      return { id, name: e.name, phase, step: e.step, endpoint: e.options.endpoint, installed: !!record || external,
        version, error: e.error, ffmpeg, mode: external ? "external" : "managed" };
    }));
  }
  async install(id: string) {
    const entry = this.entry(id);
    if (entry.pending) return;
    if (this.closed) throw new SetupError("setup_disposed", "安装控制已关闭");
    if (entry.options.serviceDirectory || entry.options.manageService === false)
      throw new SetupError("external_service", "外部服务模式使用原安装目录，不执行托管安装");
    const manifest = await this.manifest(id);
    if (!manifest.platforms.includes((process.platform + "-" + process.arch) as any))
      throw new SetupError("unsupported_platform", "此服务包首轮仅支持 macOS");
    entry.controller = new AbortController();
    const signal = entry.controller.signal;
    entry.phase = "installing"; entry.step = "校验服务资源"; entry.error = undefined;
    entry.pending = this.installEntry(id, manifest, signal).catch(async error => {
      this.failed(id, signal.aborted ? new SetupError("installation_cancelled", "安装已取消，可重新开始") : error);
    }).finally(() => { entry.pending = undefined; entry.controller = undefined; });
  }
  private async installEntry(id: string, manifest: ServiceManifest, signal: AbortSignal) {
    const entry = this.entry(id);
    await mkdir(this.root, { recursive: true, mode: 0o700 });
    const release = await installationLock(this.root, id);
    const directory = join(this.root, "services", id, manifest.sha256), staging = directory + ".preparing";
    let prepared = false, activated = false, stopped = false;
    let previous: Record | undefined;
    const backup = directory + ".repair-" + randomUUID();
    let backedUp = false;
    try {
      previous = await this.record(id);
      await rm(staging, { recursive: true, force: true });
      await mkdir(staging, { recursive: true, mode: 0o700 });
      await unpackService(manifest, new URL(manifest.payload, entry.options.manifest!), staging, signal);
      entry.step = "准备固定 Python 与隔离依赖";
      const uv = await prepareUv(this.root, signal), core = id === "core";
      const toolEnv = { UV_CACHE_DIR: join(this.root, "cache", "uv"), UV_PYTHON_INSTALL_DIR: join(this.root, "tools", "python"), UV_PYTHON_BIN_DIR: join(this.root, "tools", "python-bin"), UV_PYTHON_PREFERENCE: "only-managed", VIRTUAL_ENV: undefined };
      const findPython = async () => {
        const result = await runProgram(uv, ["python", "find", manifest.python], this.root, signal, toolEnv);
        const program = result.stdout.trim();
        if (!program.startsWith(join(this.root, "tools", "python") + "/")) throw new SetupError("python_location", "托管 Python 不在指定运行目录");
        return program;
      };
      let python: string | undefined;
      if (previous) {
        try { python = await findPython(); }
        catch {
          signal.throwIfAborted();
          await runProgram(uv, ["python", "install", "--no-bin", manifest.python], this.root, signal, toolEnv);
          python = await findPython();
        }
      }
      // Code is prepared separately. Python environments must be created at their
      // final path because editable installs and script shebangs retain that path.
      if (previous?.sha256 === manifest.sha256) {
        entry.step = "停止服务并重建环境";
        await entry.handle.stop(python); stopped = true;
        await rename(directory, backup); backedUp = true;
      } else await rm(directory, { recursive: true, force: true });
      await rename(staging, directory); prepared = true;
      const env = { ...toolEnv, UV_PROJECT_ENVIRONMENT: join(directory, core ? ".runtime/worker-venv" : ".runtime/venv"), UV_NO_PROGRESS: "1" };
      try {
        await runProgram(uv, ["sync", "--locked", "--no-dev", "--python", manifest.python, ...(core ? ["--project", join(directory, "python")] : [])], directory, signal, env);
      } catch (error: any) {
        await mkdir(join(this.root, "logs"), { recursive: true, mode: 0o700 });
        await appendFile(join(this.root, "logs", id + "-install.log"), String(error.stderr || error.code || "Installation failed").replace(/(Bearer\s+|[?&](?:token|key)=)[^\s&]+/gi, "$1[redacted]") + "\n", { mode: 0o600 });
        throw new SetupError("python_install", "Python 环境安装失败，请检查网络并重试；诊断保存在运行目录的 logs 中");
      }
      signal.throwIfAborted();
      python ||= await findPython();
      entry.step = "切换已校验的服务版本";
      if (previous && !stopped) { await entry.handle.stop(python); stopped = true; }
      await privateJson(this.recordPath(id), { directory, sha256: manifest.sha256, version: manifest.version, account: previous?.account, python });
      entry.step = "启动并检查服务";
      await entry.handle.start(); activated = true;
    } catch (error) {
      const detail = String((error as any)?.stderr || (error as any)?.message || "Installation failed").slice(-16384)
        .replace(/(Bearer\s+|[?&](?:token|key|api_key)=)[^\s&]+/gi, "$1[redacted]")
        .replace(/(https?:\/\/)[^/\s:@]+:[^@\s/]+@/gi, "$1[redacted]@");
      await mkdir(join(this.root, "logs"), { recursive: true, mode: 0o700 });
      await appendFile(join(this.root, "logs", id + "-install.log"), detail + "\n", { mode: 0o600 });
      if (prepared) await entry.handle.stop().catch(() => {});
      if (backedUp) {
        await rm(directory, { recursive: true, force: true });
        await rename(backup, directory); backedUp = false;
      }
      if (previous) {
        await privateJson(this.recordPath(id), previous);
        if (stopped) await entry.handle.start().catch(() => {});
      } else await rm(this.recordPath(id), { force: true });
      throw error;
    } finally {
      await rm(staging, { recursive: true, force: true });
      if (backedUp && activated) await rm(backup, { recursive: true, force: true });
      if (!activated && prepared && previous?.sha256 !== manifest.sha256) await rm(directory, { recursive: true, force: true });
      await release();
    }
  }
  cancel(id: string) { this.entry(id).controller?.abort(new SetupError("installation_cancelled", "安装已取消")); }
  async unregister(id: string) {
    const entry = this.entry(id);
    entry.controller?.abort();
    await entry.pending;
    await entry.handle.stop();
    this.entries.delete(id);
  }
  async wait(id: string) { await this.entry(id).pending; }
  async start(id: string) { await this.entry(id).handle.start(); }
  async stop(id: string) { await this.entry(id).handle.stop(); }
  async providerCommand(id: string, args: string[]) {
    const options = await this.launchOptions(id);
    let directory = options.serviceDirectory;
    if (!directory) {
      const legacy = JSON.parse(await readFile(options.installation, "utf8"));
      directory = formalDirectory(legacy.repository);
    }
    const module = id === "core" ? "dsh_core_worker" : (await this.manifest(id)).module;
    const program = join(directory!, id === "core" ? ".runtime/worker-venv/bin/python" : ".runtime/venv/bin/python");
    return runProgram(program, ["-m", module + ".cli", ...args,
      ...(options.stateRoot ? [id === "core" ? "--root" : "--state-root", options.stateRoot] : []),
      "--port", String(new URL(options.endpoint).port)], directory!, undefined,
      { PYTHONPATH: join(directory!, "python") });
  }
  async openDashboard() {
    const output = join(this.root, "tickets", randomUUID() + ".json");
    try {
      await this.providerCommand("core", ["dashboard", "--no-open", "--output", output]);
      return await readPrivateJson(output);
    } finally { await rm(output, { force: true }); }
  }
  endpoint(id: string) { return this.entry(id).options.endpoint; }
  async applyAuthorization(id: string, value: unknown) {
    const input = join(this.root, "tickets", randomUUID() + ".json");
    try {
      await privateJson(input, value);
      await this.providerCommand(id, ["connect-core", "--authorization-file", input]);
    } finally { await rm(input, { force: true }); }
  }
  async authorize(id: string, account: string) {
    const record = await this.record(id);
    if (record) {
      if (record.account && record.account !== account) throw new SetupError("account_mismatch", "安装已属于其他账号，不会自动转移私人数据");
      await privateJson(this.recordPath(id), { ...record, account });
    }
  }
  async dispose() {
    this.closed = true;
    for (const e of this.entries.values()) e.controller?.abort();
    await Promise.all([...this.entries.values()].map(e => e.pending));
  }
}
