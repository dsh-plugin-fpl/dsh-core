import { test, expect } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
let child: ChildProcess, origin: string, state: string;
test.beforeAll(async () => {
  await mkdir(resolve(root, ".local/storage-settings"), { recursive: true });
  state = await mkdtemp(resolve(root, ".local/storage-settings/state-"));
  const socket = createServer();
  await new Promise<void>((r) => socket.listen(0, "127.0.0.1", r));
  const { port } = socket.address() as { port: number };
  await new Promise<void>((r) => socket.close(() => r()));
  origin = `http://127.0.0.1:${port}`;
  child = spawn(
    resolve(root, ".runtime/worker-venv/bin/python"),
    [
      "tests/fixtures/server.py",
      "--root",
      state,
      "--port",
      String(port),
      "--web-root",
      resolve(root, "web/dist"),
      "--mock-models",
    ],
    { cwd: root, stdio: "ignore", env: {...process.env, DSH_FPL_STORAGE_CONFIG: resolve(state, "locator.json"), DSH_FPL_STORAGE_CONTROL: resolve(state, "control")} },
  );
  await expect
    .poll(async () => {
      try {
        return (await fetch(origin + "/ready")).ok;
      } catch {
        return false;
      }
    })
    .toBeTruthy();
});
test.afterAll(async () => {
  if (child && child.exitCode === null) {
    child.kill("SIGTERM");
    await new Promise((r) => child.once("exit", r));
  }
  if (state) await rm(state, { recursive: true, force: true });
});
test("storage settings remain readable in light and dark at desktop and phone widths", async ({ page }) => {
  const ticket = await readFile(resolve(state, "fixture-launch.txt"), "utf8");
  await page.goto(origin + "/#ticket=" + ticket);
  await expect(page.getByRole("heading", {name:"今天想从哪里开始？"})).toBeVisible();
  await page.goto(origin + "/storage");
  await expect(page.getByRole("heading", {name:"存储与迁移"})).toBeVisible();
  for (const width of [1440, 800, 390]) {
    await page.setViewportSize({width, height:1000});
    for (const theme of ["浅色", "深色"]) {
      await page.getByRole("button", {name:theme,exact:true}).click();
      await expect(page.getByLabel("统一存储根目录")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
      await page.screenshot({path:resolve(root, `.local/browser-results/storage-${width}-${theme}.png`),fullPage:true});
    }
  }
});
