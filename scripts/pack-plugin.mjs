import { execFileSync } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../", import.meta.url));
await mkdir(root + ".runtime/packages", { recursive: true });
execFileSync("pnpm", ["pack", "--pack-destination", root + ".runtime/packages"], { cwd: root, stdio: "inherit" });
