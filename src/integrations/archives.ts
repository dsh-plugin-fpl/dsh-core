import {
  readdir,
  lstat,
  readFile,
  mkdir,
  writeFile,
  realpath,
} from "node:fs/promises";
import { resolve, relative, isAbsolute, dirname } from "node:path";
import { zipSync, unzipSync, strToU8, strFromU8 } from "fflate";
const MAX_EXPANDED = 100 * 1024 * 1024,
  MAX_ARCHIVE = 20 * 1024 * 1024;
export async function exportSkill(skill: any) {
  const entries: Record<string, Uint8Array> = {};
  let bytes = 0;
  const base = skill.resourceBase;
  if (base?.kind === "directory") {
    const root = await realpath(base.path);
    async function walk(directory: string) {
      for (const entry of await readdir(directory, { withFileTypes: true })) {
        const path = resolve(directory, entry.name);
        const stat = await lstat(path);
        if (stat.isSymbolicLink())
          throw new Error("Skill 包含符号链接，需整理为独立文件包");
        if (stat.isDirectory()) await walk(path);
        else if (stat.isFile()) {
          const name = relative(root, path).split("\\").join("/");
          if (name.startsWith("../") || isAbsolute(name))
            throw new Error("Skill 路径无效");
          bytes += stat.size;
          if (bytes > MAX_EXPANDED || Object.keys(entries).length >= 1000)
            throw new Error("Skill 文件包超过限制");
          entries[name] = new Uint8Array(await readFile(path));
        } else throw new Error("Skill 包含不支持的文件类型");
      }
    }
    await walk(root);
  } else if (base && base.kind !== "opaque")
    throw new Error("此 Skill 的附件不能完整导出，请由插件提供打包适配器");
  if (!entries["SKILL.md"]) {
    if (base) throw new Error("Skill 文件包缺少 SKILL.md");
    entries["SKILL.md"] = strToU8(
      "---\nname: " +
        JSON.stringify(skill.name) +
        "\ndescription: " +
        JSON.stringify(skill.description || "Skill") +
        "\n---\n" +
        skill.content,
    );
  }
  const zipped = zipSync(
    Object.fromEntries(
      Object.entries(entries)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => [k, [v, { mtime: new Date("1980-01-01T00:00:00Z") }]]),
    ),
    { level: 6 },
  );
  if (zipped.length > MAX_ARCHIVE) throw new Error("Skill ZIP 超过 20 MiB");
  return Buffer.from(zipped).toString("base64");
}
export async function installSkill(archive: string, root: string) {
  const input = Buffer.from(archive, "base64");
  if (input.length > MAX_ARCHIVE) throw new Error("Skill ZIP 过大");
  let expanded = 0,
    count = 0;
  const files = unzipSync(input, {
    filter: (file) => {
      expanded += file.originalSize;
      if (++count > 1000 || expanded > MAX_EXPANDED)
        throw new Error("Skill 解压大小超过限制");
      return true;
    },
  });
  if (!files["SKILL.md"]) throw new Error("Skill ZIP 缺少 SKILL.md");
  const names = new Set<string>();
  for (const [name, content] of Object.entries(files)) {
    if (name.endsWith("/")) continue;
    if (
      !name ||
      name.includes("\\") ||
      name.includes("\0") ||
      isAbsolute(name) ||
      name.split("/").includes("..")
    )
      throw new Error("Skill ZIP 路径无效");
    const key = name.toLowerCase();
    if (names.has(key)) throw new Error("Skill ZIP 路径重复");
    names.add(key);
    const target = resolve(root, name);
    if (!target.startsWith(resolve(root) + "/"))
      throw new Error("Skill ZIP 越界");
    if (content.length > MAX_EXPANDED) throw new Error("Skill 文件过大");
  }
  await mkdir(root, { recursive: true, mode: 0o700 });
  for (const [name, content] of Object.entries(files)) {
    if (name.endsWith("/")) continue;
    const target = resolve(root, name);
    await mkdir(dirname(target), { recursive: true, mode: 0o700 });
    await writeFile(target, content, { mode: 0o600, flag: "wx" });
  }
  return strFromU8(files["SKILL.md"]);
}
