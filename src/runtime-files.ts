/** Private installation records and bounded, verified production payloads. */
import { createHash, randomUUID } from "node:crypto";
import { lstat, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve, sep } from "node:path";
import { unzipSync } from "fflate";
import { z } from "zod";

export const serviceManifestSchema = z.object({
  protocol: z.literal(1), id: z.string().regex(/^(core|dsh-[a-z0-9-]+)$/),
  name: z.string().min(1).max(120), module: z.string().regex(/^[a-z][a-z0-9_]+$/),
  port: z.number().int().min(1024).max(65535), version: z.string().min(1),
  package: z.string().regex(/^@dsh-plugin-fpl\/dsh-[a-z0-9-]+$/),
  platforms: z.array(z.enum(["darwin-arm64", "darwin-x64"])),
  python: z.literal("3.13.15"), payload: z.literal("service.zip"),
  sha256: z.string().regex(/^[a-f0-9]{64}$/),
  byte_size: z.number().int().positive().max(128 * 1024 * 1024),
  expanded_size: z.number().int().positive().max(512 * 1024 * 1024),
  files: z.record(z.string(), z.string().regex(/^[a-f0-9]{64}$/)),
}).strict();
export type ServiceManifest = z.infer<typeof serviceManifestSchema>;
export const sha256 = (bytes: Uint8Array | string) => createHash("sha256").update(bytes).digest("hex");
export class SetupError extends Error {
  constructor(public code: string, message: string) { super(message); }
}
export async function privateJson(path: string, value: unknown) {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const temporary = path + "." + randomUUID();
  try {
    await writeFile(temporary, JSON.stringify(value) + "\n", { mode: 0o600, flag: "wx" });
    await rename(temporary, path);
  } finally { await rm(temporary, { force: true }); }
}
export async function readPrivateJson(path: string): Promise<any> {
  const info = await lstat(path);
  if (!info.isFile() || info.isSymbolicLink() || (info.mode & 0o077))
    throw new SetupError("private_file", "安装记录必须是仅当前用户可读的普通文件");
  return JSON.parse(await readFile(path, "utf8"));
}
export function formalDirectory(value: string) {
  if (!isAbsolute(value) || value.split(sep).includes(".local"))
    throw new SetupError("installation_path", "服务安装目录必须是正式绝对路径，不能依赖 .local");
  return resolve(value);
}
/** Cross-process exclusion; a dead installer can be resumed, a live owner is never stolen. */
export async function installationLock(root: string, id: string) {
  const path = join(root, "locks", id + ".lock");
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  try { await mkdir(path, { mode: 0o700 }); }
  catch (error: any) {
    if (error.code !== "EEXIST") throw error;
    const owner = await readPrivateJson(join(path, "owner.json")).catch(() => null);
    if (!owner || !Number.isInteger(owner.pid))
      throw new SetupError("installation_busy", "另一个安装过程尚未完成，请稍后重试");
    try { process.kill(owner.pid, 0); }
    catch (error: any) {
      if (error.code !== "ESRCH") throw error;
      await rm(path, { recursive: true });
      return installationLock(root, id);
    }
    throw new SetupError("installation_busy", "另一个安装过程正在管理此服务");
  }
  await privateJson(join(path, "owner.json"), { pid: process.pid });
  return () => rm(path, { recursive: true, force: true });
}
export async function unpackService(manifest: ServiceManifest, source: URL, target: string, signal: AbortSignal) {
  signal.throwIfAborted();
  const stat = await lstat(source);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size !== manifest.byte_size)
    throw new SetupError("payload_size", "服务载荷大小不匹配");
  const bytes = await readFile(source);
  if (sha256(bytes) !== manifest.sha256) throw new SetupError("payload_digest", "服务载荷摘要不匹配");
  let size = 0;
  const files = unzipSync(bytes, { filter(entry) {
    if (!(entry.name in manifest.files) || entry.originalSize > manifest.expanded_size)
      throw new SetupError("payload_manifest", "服务载荷包含未登记的文件");
    size += entry.originalSize;
    if (size > manifest.expanded_size) throw new SetupError("payload_size", "服务展开大小超过清单");
    return true;
  } });
  if (Object.keys(files).length !== Object.keys(manifest.files).length || size !== manifest.expanded_size)
    throw new SetupError("payload_manifest", "服务载荷文件不完整");
  for (const [path, data] of Object.entries(files)) {
    signal.throwIfAborted();
    if (!path || path.startsWith("/") || path.includes("\\") || path.split("/").some(p => !p || p === "." || p === "..") || sha256(data) !== manifest.files[path])
      throw new SetupError("payload_path", "服务载荷路径或文件摘要无效");
    const output = join(target, path);
    await mkdir(dirname(output), { recursive: true, mode: 0o700 });
    await writeFile(output, data, { mode: 0o600, flag: "wx" });
  }
}
