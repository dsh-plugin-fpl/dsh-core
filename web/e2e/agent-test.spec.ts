import { test, expect } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
let child: ChildProcess, origin: string, state: string;
test.beforeAll(async () => {
  await mkdir(resolve(root, ".local/remove-run-records"), { recursive: true });
  state = await mkdtemp(resolve(root, ".local/remove-run-records/state-"));
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
    await new Promise((r) => child.once("exit", r));
  }
  if (state) await rm(state, { recursive: true, force: true });
});
function task(
  id: string,
  application = "core",
  status = "completed",
  kind = "model",
) {
  return {
    id,
    request: {
      request_id: id,
      application_id: application,
      session_id: "model-test",
      kind,
      capability: "chat",
      connection_id: kind === "agent" ? "codex" : "fixture",
      model: "fixture-model",
      prompt: id,
      parameters: {},
      resource_ids: [],
      tools: [],
      messages: [],
    },
    status,
    progress: 30,
    text: "已保留的输出",
    result: null,
    error: null as null | { code: string; message: string },
    created_at: 1795413509,
    updated_at: 1795413509,
    provider_task_id: "",
    can_resume: false,
  };
}
test("移除运行记录入口，智能体测试直接查看和控制结果", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  let listReads = 0;
  await page.route("**/v1/tasks", async (route) => {
    if (route.request().method() === "POST") {
      expect(route.request().postDataJSON().connection_id).toBe("codex");
      await route.fulfill({
        json: task("submitted", "core", "running", "agent"),
      });
    } else {
      listReads++;
      await route.fulfill({ json: { items: [] } });
    }
  });
  const current = {
    ...task("submitted", "core", "running", "agent"),
    can_resume: false,
  };
  await page.route("**/v1/tasks/submitted", (route) =>
    route.fulfill({ json: current }),
  );
  let failCancel = true;
  await page.route("**/v1/rpc", async (route) => {
    const { method, params } = route.request().postDataJSON();
    expect(params.id).toBe("submitted");
    if (method === "task.cancel" && failCancel) {
      failCancel = false;
      await route.fulfill({
        status: 409,
        json: { detail: "停止失败，请重试" },
      });
      return;
    }
    current.status = method === "task.cancel" ? "cancelled" : "completed";
    current.can_resume = false;
    await route.fulfill({ json: current });
  });
  const ticket = await readFile(resolve(state, "fixture-launch.txt"), "utf8");
  await page.goto(origin + "/tasks?task=obsolete#ticket=" + ticket);
  await expect(
    page.getByRole("heading", { name: "今天想从哪里开始？" }),
  ).toBeVisible();
  await expect(page.getByText("运行记录", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "最近运行" })).toHaveCount(0);
  expect(listReads).toBe(0);
  await page.route("**/v1/code-agents", (route) =>
    route.fulfill({
      json: {
        can_enable: true,
        items: [
          {
            id: "codex",
            name: "Codex",
            enabled: true,
            available: true,
            configured: true,
            status: "ready",
            models: [],
            purposes: [],
            executable: "",
            version: "1",
            authenticated: true,
            message: "已连接",
          },
        ],
      },
    }),
  );
  await page.goto(origin + "/code-agents");
  await expect(
    page.getByRole("button", { name: "运行记录", exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("测试内容", { exact: true }).fill("验证测试入口");
  await page.getByRole("button", { name: "提交测试", exact: true }).click();
  const result = page.getByRole("region", { name: "本次测试结果" });
  await expect(result.getByText("已保留的输出", { exact: true })).toBeVisible();
  await expect(page.locator('a[href^="/tasks"]')).toHaveCount(0);
  await result.getByRole("button", { name: "停止测试", exact: true }).click();
  await expect(
    result.getByText("停止失败，请重试", { exact: true }),
  ).toBeVisible();
  await result.getByRole("button", { name: "停止测试", exact: true }).click();
  await expect(result.getByText("已取消", { exact: true })).toBeVisible();
  current.status = "interrupted";
  current.can_resume = true;
  current.error = { code: "bridge_disconnected", message: "DSH 连接已断开" };
  await page.reload();
  await page.getByLabel("测试内容", { exact: true }).fill("读取可恢复结果");
  await page.getByRole("button", { name: "提交测试", exact: true }).click();
  await expect(
    result.getByText("DSH 连接已断开", { exact: true }),
  ).toBeVisible();
  await expect(result.getByText("已保留的输出", { exact: true })).toBeVisible();
  await result.getByRole("button", { name: "恢复查询", exact: true }).click();
  await expect(result.getByText("已完成", { exact: true })).toBeVisible();
  for (const theme of ["浅色", "深色"]) {
    await page.getByRole("button", { name: theme, exact: true }).click();
    for (const width of [1440, 860, 390]) {
      await page.setViewportSize({ width, height: 900 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBeTruthy();
      await page.screenshot({
        path: resolve(
          root,
          `.local/remove-run-records/agent-${theme}-${width}.png`,
        ),
        fullPage: true,
      });
    }
  }
  expect(errors).toEqual([]);
});
