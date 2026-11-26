import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const pkg = JSON.parse(await readFile(resolve(root, "package.json"), "utf8"));
const name =
  "fpl-core-service-" +
  pkg.version +
  "-" +
  new Date().toISOString().replace(/[-:TZ.]/g, "");
const releases = resolve(root, ".runtime/releases");
await mkdir(releases, { recursive: true });
const output = resolve(releases, name);
await mkdir(output);
await cp(
  resolve(root, "python/dsh_core_worker"),
  resolve(output, "python/dsh_core_worker"),
  {
    recursive: true,
    filter: (p) => !p.includes("__pycache__") && !p.endsWith(".pyc"),
  },
);
await cp(resolve(root, "python/fpl_core"), resolve(output, "python/fpl_core"), {recursive: true, filter: p => !p.includes("__pycache__")});
for (const item of ["pyproject.toml", "uv.lock"])
  await cp(resolve(root, "python", item), resolve(output, "python", item));
await cp(resolve(root, "web/dist"), resolve(output, "web/dist"), {
  recursive: true,
});
await mkdir(resolve(output, "scripts"));
await cp(
  resolve(root, "scripts/core.mjs"),
  resolve(output, "scripts/core.mjs"),
);
await writeFile(
  resolve(output, "package.json"),
  JSON.stringify(
    {
      name: "fpl-core-service",
      private: true,
      version: pkg.version,
      type: "module",
      scripts: Object.fromEntries(
        [
          "start",
          "start:with-dsh",
          "dashboard",
          "status",
          "stop",
          "setup:service",
        ].map((n) => [n, pkg.scripts[n]]),
      ),
    },
    null,
    2,
  ) + "\n",
);
await writeFile(
  resolve(output, "README.md"),
  "# FPL Core 本机运行包\n\n安装 uv、Node.js 和 pnpm，然后在此目录运行：\n\n```sh\npnpm setup:service\npnpm start:with-dsh\n```\n\n服务也可以通过 `pnpm start` 单独启动；`pnpm status` 查看状态，`pnpm dashboard` 打开看板，`pnpm stop` 停止。配置、密钥和业务状态在此目录的 `.runtime/core/`，初始为空。此本机运行包包含当前品牌资源，沿用内部开发交接范围。\n",
);
const packages = resolve(root, ".runtime/packages");
await mkdir(packages, { recursive: true });
const archive = resolve(packages, name + ".tar.gz");
const result = spawnSync("tar", ["-czf", archive, "-C", releases, name], {
  stdio: "inherit",
});
if (result.status !== 0) process.exit(result.status ?? 1);
console.log(JSON.stringify({ directory: output, archive }));
