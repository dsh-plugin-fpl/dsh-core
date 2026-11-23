import { cp, mkdir, access } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(resolve(root, "web/package.json"));
const vad = dirname(require.resolve("@ricky0123/vad-web/package.json"));
const target = resolve(root, "web/public/voice-vad");
await mkdir(target, { recursive: true });
for (const name of ["silero_vad_v5.onnx", "vad.worklet.bundle.min.js"])
  await cp(resolve(vad, "dist", name), resolve(target, name));
await cp(
  require.resolve("onnxruntime-web/ort-wasm-simd-threaded.wasm"),
  resolve(target, "ort-wasm-simd-threaded.wasm"),
);
const branding = resolve(root, ".runtime/assets/brand");
try {
  await access(branding);
  await cp(branding, resolve(root, "web/public/brand"), { recursive: true });
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
