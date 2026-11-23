import { test, expect } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
let child: ChildProcess, origin: string, state: string;
test.beforeAll(async () => {
  await mkdir(resolve(root, ".local/run-records"), { recursive: true });
  state = await mkdtemp(resolve(root, ".local/run-records/state-"));
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
test("运行筛选、操作、详情、测试入口与布局", async ({ page }) => {
  const failures: string[] = [];
  page.on("pageerror", (e) => failures.push(e.message));
  const running = task("进行中的测试", "core", "running");
  const interrupted = {
    ...task(
      "插件中断记录",
      "b-00000000-0000-0000-0000-000000000001-visual",
      "interrupted",
    ),
    error: { code: "bridge_disconnected", message: "DSH 连接已断开" },
    can_resume: true,
  };
  let items = [running, interrupted, task("完成的测试")];
  const calls: string[] = [];
  await page.route("**/v1/tasks", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      expect(body.connection_id).toBe("codex");
      calls.push(body.request_id);
      await route.fulfill({
        json: task("submitted", "core", "queued", "agent"),
      });
    } else await route.fulfill({ json: { items } });
  });
  let failCancel = true;
  await page.route("**/v1/rpc", async (route) => {
    const body = route.request().postDataJSON();
    if (body.method === "task.cancel" && failCancel) {
      failCancel = false;
      await route.fulfill({
        status: 409,
        json: { detail: "停止失败，请重试" },
      });
      return;
    }
    const row = items.find((t) => t.id === body.params.id)!;
    row.status = body.method === "task.cancel" ? "cancelled" : "processing";
    row.can_resume = false;
    await route.fulfill({ json: row });
  });
  const ticket = await readFile(resolve(state, "fixture-launch.txt"), "utf8");
  await page.goto(origin + "/tasks#ticket=" + ticket);
  await expect(
    page.getByRole("heading", { name: "运行记录", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "智能体测试" })).toHaveCount(
    0,
  );
  await expect(
    page
      .locator(".studio-sidebar__rail")
      .getByRole("button", { name: "任务", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("combobox", { name: "运行来源", exact: true }).click();
  await page
    .getByRole("option", { name: "DSH 应用 · visual", exact: true })
    .click();
  await expect(page.locator(".core-task-row")).toHaveCount(1);
  await page.getByRole("combobox", { name: "运行状态", exact: true }).click();
  await page.getByRole("option", { name: "已完成", exact: true }).click();
  await expect(
    page.getByText("没有符合筛选条件的运行记录。", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "清除筛选" }).click();
  await expect(page.locator(".core-task-row")).toHaveCount(3);
  await page
    .getByRole("button", { name: "停止 进行中的测试", exact: true })
    .click();
  await expect(
    page.getByText("停止失败，请重试", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "停止 进行中的测试", exact: true })
    .click();
  await expect(
    page.locator(".core-task-row").filter({ hasText: "进行中的测试" }),
  ).toContainText("已取消");
  await page.getByRole("button", { name: "插件中断记录", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "运行详情" });
  await expect(
    dialog.getByText("DSH 连接已断开", { exact: true }),
  ).toBeVisible();
  await expect(dialog.getByText("已保留的输出", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "恢复查询", exact: true }).click();
  await expect(
    dialog.getByRole("button", { name: "恢复查询", exact: true }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.goto(origin + "/tasks?task=" + encodeURIComponent("完成的测试"));
  await expect(page.getByRole("dialog", { name: "运行详情" })).toBeVisible();
  await expect(
    page.getByRole("link", { name: "打开来源", exact: true }),
  ).toHaveAttribute("href", "/model-test?mode=chat");
  await page.keyboard.press("Escape");
  expect(page.url()).not.toContain("task=");
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
        path: resolve(root, `.local/run-records/records-${theme}-${width}.png`),
        animations: "disabled",
      });
    }
  }
  items = [];
  await page.goto(origin + "/tasks");
  await expect(page.getByText("暂无运行记录", { exact: true })).toBeVisible();
  await expect(page.getByText("运行记录筛选", { exact: true })).toHaveCount(0);
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
    page.getByRole("heading", { name: "智能体测试", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "提交测试", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("测试内容", { exact: true }).fill("验证测试入口");
  await page.getByRole("button", { name: "提交测试", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "查看本次运行", exact: true }),
  ).toHaveAttribute("href", "/tasks?task=submitted");
  expect(calls).toHaveLength(1);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: resolve(root, ".local/run-records/agent-test-narrow.png"),
    animations: "disabled",
    fullPage: true,
  });
  // Use real persistent conversations to verify the source link selects the original,
  // even when another conversation is the newest one.
  await page.unroute("**/v1/tasks");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(origin + "/model-service");
  await page.getByRole("button", { name: "添加接入连接", exact: true }).click();
  const providerDialog = page.getByRole("dialog");
  await providerDialog.getByLabel("连接名称").fill("运行记录来源测试");
  await providerDialog
    .getByLabel("服务地址", { exact: true })
    .fill("https://api.example.com/v1");
  await providerDialog
    .getByLabel("API Key", { exact: true })
    .fill("fixture-secret");
  await providerDialog
    .getByRole("button", { name: "保存", exact: true })
    .click();
  await page.getByRole("tab", { name: "模型目录", exact: true }).click();
  await page.getByRole("button", { name: "添加模型", exact: true }).click();
  const modelDialog = page.getByRole("dialog");
  await modelDialog.getByLabel("模型标识").fill("fixture-source");
  await modelDialog.getByLabel("显示名称").fill("来源测试模型");
  await modelDialog.getByRole("button", { name: "保存", exact: true }).click();
  const catalog = await (
    await page.request.get(origin + "/v1/model-providers")
  ).json();
  const modelId = catalog.providers[0].models[0].id;
  await page.goto(origin + "/model-test?model=" + encodeURIComponent(modelId));
  await page.getByPlaceholder("输入要测试的问题").fill("原始对话内容");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByText("独立 Core 测试回复，已保留会话历史。", { exact: true }),
  ).toBeVisible();
  const saved = await (await page.request.get(origin + "/v1/tasks")).json();
  const original = saved.items.find(
    (t: { request: { prompt: string } }) => t.request.prompt === "原始对话内容",
  );
  await page.goto(origin + "/model-test?model=" + encodeURIComponent(modelId));
  await page.getByPlaceholder("输入要测试的问题").fill("另一个新对话");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByText("独立 Core 测试回复，已保留会话历史。", { exact: true }),
  ).toBeVisible();
  await page.goto(origin + "/tasks?task=" + encodeURIComponent(original.id));
  await page.getByRole("link", { name: "打开来源", exact: true }).click();
  await expect(
    page
      .locator(".chat-message.user")
      .getByText("原始对话内容", { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator(".chat-message.user")
      .getByText("另一个新对话", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("独立 Core 测试回复，已保留会话历史。", { exact: true }),
  ).toBeVisible();
  expect(failures).toEqual([]);
});
