import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
if (["test", "contracts"].includes(process.argv[2]))
  mkdirSync(resolve(root, ".local"), { recursive: true });
const environment = {
  ...process.env,
  UV_CACHE_DIR: process.env.UV_CACHE_DIR || resolve(root, ".runtime/uv-cache"),
  UV_PROJECT_ENVIRONMENT: resolve(root, ".runtime/worker-venv"),
};
const command = process.argv[2] || "start",
  extra = process.argv.slice(3);
let args;
if (command === "setup")
  args = ["sync", "--project", resolve(root, "python"), "--locked"];
else if (command === "test")
  args = [
    "run",
    "--project",
    resolve(root, "python"),
    "--locked",
    "python",
    "-m",
    "pytest",
    "-c",
    resolve(root, "python/pyproject.toml"),
    resolve(root, "python/tests"),
    "--basetemp=" + resolve(root, ".local/pytest"),
    "-q",
    ...extra,
  ];
else if (command === "contracts")
  args = [
    "run",
    "--project",
    resolve(root, "python"),
    "--locked",
    "python",
    resolve(root, "scripts/export_contracts.py"),
    ...extra,
  ];
else
  args = [
    "run",
    "--project",
    resolve(root, "python"),
    "--locked",
    "--no-dev",
    "python",
    "-m",
    "dsh_core_worker.cli",
    command,
    ...extra,
  ];
if (command === "contracts") {
  const result = spawnSync("uv", args, {
    cwd: root,
    env: environment,
    stdio: "inherit",
  });
  if (result.status !== 0) process.exit(result.status ?? 1);
  const types = spawnSync(
    "pnpm",
    [
      "--dir",
      "web",
      "exec",
      "openapi-typescript",
      "src/generated/openapi.json",
      "-o",
      "src/generated/api.ts",
    ],
    { cwd: root, stdio: "inherit" },
  );
  process.exit(types.status ?? 1);
}
const result = spawnSync("uv", args, {
  cwd: root,
  env: environment,
  stdio: "inherit",
});
if (result.error) {
  process.stderr.write("无法运行 uv，请先安装 uv。\n");
  process.exit(1);
}
process.exit(result.status ?? 1);
