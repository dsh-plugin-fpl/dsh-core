import { access } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
for (const name of [
  "brand-mark.png",
  "icon.webp",
  "home/kurisu-idle.png",
  "home/kurisu-idle.webm",
  "loading/divergence-meter.webp",
  "loading/dr-pepper.webp",
  "loading/flip-phone.webp",
  "loading/gel-banana.webp",
  "loading/kurisu-run.png",
  "loading/kurisu-run.webm",
  "loading/kurisu-run.webp",
  "loading/loading-dial.svg",
  "loading/metal-upa.webp",
  "loading/phonewave.webp",
  "loading/pocket-watch.webp",
  "loading/time-machine.webp",
]) {
  try {
    await access(resolve(root, ".runtime/assets/brand", name));
  } catch {
    process.stderr.write(
      "本机品牌资源不完整，请先安装到 .runtime/assets/brand/。通用构建使用 pnpm build。\n",
    );
    process.exit(1);
  }
}
const result = spawnSync("pnpm", ["build"], {
  cwd: root,
  env: { ...process.env, DSH_FPL_INCLUDE_LOCAL_BRAND: "1" },
  stdio: "inherit",
});
if (result.error) process.stderr.write("无法运行 pnpm，请检查本机安装。\n");
process.exit(result.status ?? 1);
