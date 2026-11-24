/** Bootstrap only the explicit installer environment; never change global tools. */
import { spawn } from "node:child_process";
import { chmod, mkdir, open, rename, rm, stat } from "node:fs/promises";
import { join } from "node:path";
import { createHash, randomUUID } from "node:crypto";
import { SetupError, installationLock } from "./runtime-files.js";
export async function runProgram(program: string, args: string[], cwd: string, signal?: AbortSignal, env: NodeJS.ProcessEnv = {}) {
  signal?.throwIfAborted();
  return new Promise<{ stdout: string; stderr: string }>((resolve, reject) => {
    // Wait for close after abort: uv can still be writing its environment after
    // an AbortSignal rejects execFile's promise. Terminate its whole process group.
    const child = spawn(program, args, { cwd, detached: process.platform !== "win32", stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, ...env, ELECTRON_RUN_AS_NODE: "1" } });
    let stdout = "", stderr = "", size = 0, failure: any, force: ReturnType<typeof setTimeout> | undefined;
    const kill = (kind: NodeJS.Signals) => {
      try { if (process.platform !== "win32" && child.pid) process.kill(-child.pid, kind); else child.kill(kind); }
      catch (error: any) { if (error.code !== "ESRCH") failure ||= error; }
    };
    const stop = (error: any) => {
      failure ||= error; kill("SIGTERM");
      force ||= setTimeout(() => kill("SIGKILL"), 3000);
    };
    const abort = () => stop(signal?.reason || new DOMException("Aborted", "AbortError"));
    const timeout = setTimeout(() => stop(new SetupError("program_timeout", "服务命令执行超时")), 15 * 60 * 1000);
    const collect = (stream: "stdout" | "stderr", chunk: Buffer) => {
      size += chunk.length;
      if (size > 512 * 1024) return stop(new SetupError("program_output", "服务命令输出超过限制"));
      if (stream === "stdout") stdout += chunk.toString(); else stderr += chunk.toString();
    };
    child.stdout.on("data", chunk => collect("stdout", chunk));
    child.stderr.on("data", chunk => collect("stderr", chunk));
    child.on("error", error => { failure ||= error; });
    child.on("close", code => {
      clearTimeout(timeout); if (force) clearTimeout(force); signal?.removeEventListener("abort", abort);
      if (failure || code !== 0) {
        const error = failure || Object.assign(new Error("Service command failed"), { code });
        reject(Object.assign(error, { stdout, stderr }));
      } else resolve({ stdout, stderr });
    });
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
  });
}
const uvAssets: Record<string, { url: string; sha256: string }> = {
  arm64: { url: "https://github.com/astral-sh/uv/releases/download/0.12.18/uv-aarch64-apple-darwin.tar.gz", sha256: "cf40e0c6a202190ccd9e0406dcfdd5b2d6668a9a5c779b17948963df32aafe5b" },
  x64: { url: "https://github.com/astral-sh/uv/releases/download/0.12.18/uv-x86_64-apple-darwin.tar.gz", sha256: "2e4108f5395397c8bc5d43bf83d3bdbb2d0e92b90d0efa607756be704905fa33" },
};
export async function prepareUv(root: string, signal: AbortSignal): Promise<string> {
  try {
    const { stdout } = await runProgram("uv", ["--version"], root, signal);
    if (/^uv 0\.12\.18(?:\s|$)/.test(stdout.trim())) return "uv";
  } catch (error: any) {
    signal.throwIfAborted();
    if (error.code !== "ENOENT") throw new SetupError("uv_unusable", "现有 uv 无法执行，请检查路径或重试");
  }
  const asset = process.platform === "darwin" && uvAssets[process.arch];
  if (!asset) throw new SetupError("unsupported_platform", "托管安装首轮仅支持 macOS");
  const directory = join(root, "tools", "uv-0.12.18-" + process.arch), program = join(directory, "uv");
  const release = await installationLock(root, "uv");
  try {
    try { if ((await stat(program)).isFile()) return program; } catch (e: any) { if (e.code !== "ENOENT") throw e; }
    const temporary = directory + "." + randomUUID();
    await mkdir(temporary, { recursive: true, mode: 0o700 });
    try {
      const archive = join(temporary, "uv.tar.gz"), hash = createHash("sha256");
      const response = await fetch(asset.url, { signal: AbortSignal.any([signal, AbortSignal.timeout(120000)]) });
      if (!response.ok || !response.body) throw new SetupError("uv_download", "无法下载固定版本 uv，请检查网络后重试");
      const file = await open(archive, "wx", 0o600);
      let size = 0;
      try {
        for await (const chunk of response.body) {
          signal.throwIfAborted(); size += chunk.byteLength;
          if (size > 64 * 1024 * 1024) throw new SetupError("uv_download", "uv 下载超过大小限制");
          hash.update(chunk); await file.write(chunk);
        }
      } finally { await file.close(); }
      if (hash.digest("hex") !== asset.sha256) throw new SetupError("uv_digest", "uv 下载摘要校验失败");
      await runProgram("tar", ["-xzf", archive, "--strip-components=1"], temporary, signal);
      await rm(archive); await chmod(join(temporary, "uv"), 0o700);
      await rename(temporary, directory);
    } finally { await rm(temporary, { recursive: true, force: true }); }
    return program;
  } finally { await release(); }
}
