import { cp, mkdir, access, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const branding = resolve(root, ".runtime/assets/brand");
const publicBrand = resolve(root, "web/public/brand");
await rm(publicBrand, { recursive: true, force: true });
await mkdir(publicBrand, { recursive: true });
await cp(resolve(root, "src/icon.webp"), resolve(publicBrand, "icon.webp"));
try {
  if (process.env.DSH_FPL_INCLUDE_LOCAL_BRAND !== "1") throw Object.assign(new Error("Public assets"), { code: "ENOENT" });
  await access(branding);
  await cp(branding, resolve(root, "web/public/brand"), { recursive: true });
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
