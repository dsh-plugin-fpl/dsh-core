/** Production payloads are created before npm pack/publish; no machine paths or secrets. */
import { execFileSync } from "node:child_process";
import { cp, mkdir, rm, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../", import.meta.url));
const config = JSON.parse(await readFile(root + "service.config.json", "utf8"));
execFileSync("pnpm", ["build"], { cwd: root, stdio: "inherit" });
execFileSync("python3", ["scripts/package-service.py"], { cwd: root, stdio: "inherit" });
const target = root + (config.id === "core" ? "lib/runtime" : "src/runtime");
await rm(target, { recursive: true, force: true });
await mkdir(target, { recursive: true });
await cp(root + ".runtime/service-package", target, { recursive: true });
