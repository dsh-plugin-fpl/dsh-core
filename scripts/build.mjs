import { build } from "esbuild";
import { cp, mkdir, rm, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
await rm("lib", { recursive: true, force: true });
await mkdir("lib", { recursive: true });
try {
  if (process.env.DSH_FPL_INCLUDE_LOCAL_BRAND !== "1") throw Object.assign(new Error("Use distributable icon"), { code: "ENOENT" });
  await cp(".runtime/assets/brand/icon.webp", "lib/icon.webp");
} catch (error) {
  if (error.code !== "ENOENT") throw error;
  await cp("src/icon.webp", "lib/icon.webp");
}
execFileSync("pnpm", ["exec", "tsc", "--emitDeclarationOnly"], {
  stdio: "inherit",
});
await build({
  entryPoints: [
    "src/index.ts",
    "src/lifecycle.ts",
    "src/contracts.ts",
    "src/remote.ts",
    "src/connection.ts",
    "src/runtime-manager.ts",
    "src/native-tools.ts",
    "src/runtime-bootstrap.ts",
  ],
  outdir: "lib",
  bundle: true,
  format: "esm",
  platform: "node",
  target: "node22",
  packages: "external",
  sourcemap: true,
});
const client = await build({
  entryPoints: ["src/client/index.tsx"],
  bundle: true,
  format: "cjs",
  platform: "browser",
  target: "es2022",
  external: ["react", "react/jsx-runtime", "zod"],
  write: false,
});
await writeFile(
  "lib/client.js",
  `window.__ModuleLoader__.load({id:"@dsh-plugin-fpl/dsh-core",factory:(require)=>{var module={exports:{}};var exports=module.exports;\n${client.outputFiles[0].text}\nreturn module.exports;}});\n`,
);
