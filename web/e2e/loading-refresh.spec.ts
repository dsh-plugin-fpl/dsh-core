import { test, expect } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { createServer } from "node:net";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
let child: ChildProcess, origin: string, state: string;
let branded = false;

test.beforeAll(async () => {
  await mkdir(resolve(root, ".local/refresh-loading"), { recursive: true });
  state = await mkdtemp(resolve(root, ".local/refresh-loading/state-"));
  const socket = createServer();
  await new Promise<void>((done) => socket.listen(0, "127.0.0.1", done));
  const { port } = socket.address() as { port: number };
  await new Promise<void>((done) => socket.close(() => done()));
  origin = `http://127.0.0.1:${port}`;
  branded = (
    await readFile(resolve(root, "web/dist/index.html"), "utf8")
  ).includes("fpl-scene-runner");
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
    { cwd: root, stdio: "ignore" },
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
    await new Promise((done) => child.once("exit", done));
  }
  if (state) await rm(state, { recursive: true, force: true });
});

test("initial HTML and browser reload show feedback before React downloads", async ({
  page,
}) => {
  let release = () => {};
  let held = Promise.resolve();
  let mainRequests = 0;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route(/\/assets\/main-[^/]+\.js$/, async (route) => {
    mainRequests++;
    await held;
    await route.continue();
  });
  try {
    for (const refresh of [false, true]) {
      held = new Promise<void>((done) => {
        release = done;
      });
      const expected = mainRequests + 1;
      if (refresh) await page.reload({ waitUntil: "commit" });
      else await page.goto(origin + "/", { waitUntil: "commit" });
      await expect.poll(() => mainRequests).toBe(expected);
      const loader = page.locator(".fpl-boot-loading");
      await expect(loader).toBeVisible();
      if (branded) {
        await expect(loader.locator(".fpl-scene-prop")).toHaveCount(8);
        await expect(loader.locator(".fpl-loading-character")).toHaveAttribute(
          "data-playing",
          "true",
        );
        await page.emulateMedia({ reducedMotion: "reduce" });
        await expect(loader.locator(".fpl-loading-scene")).toHaveAttribute(
          "data-paused",
          "true",
        );
        await expect(
          loader.locator(".fpl-loading-character img"),
        ).toBeVisible();
        await page.emulateMedia({ reducedMotion: "no-preference" });
      } else {
        await expect(loader.locator(".fpl-scene-prop")).toHaveCount(0);
      }
      release();
      await expect(
        page.getByRole("heading", { name: "今天想从哪里开始？" }),
      ).toBeVisible();
      await expect(loader).toHaveCount(0);
    }
    expect(errors).toEqual([]);
  } finally {
    release();
  }
});

test("an application module failure offers a manual reload", async ({
  page,
}) => {
  await page.route(/\/assets\/main-[^/]+\.js$/, (route) => route.abort());
  await page.goto(origin + "/");
  await expect(page.getByRole("alert")).toContainText("页面资源加载失败");
  await expect(
    page.getByRole("button", { name: "重新加载", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".fpl-boot-loading")).toHaveCount(0);
});
