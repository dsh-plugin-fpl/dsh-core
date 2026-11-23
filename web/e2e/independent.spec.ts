import WebSocket from "ws";
import { zipSync, strToU8 } from "fflate";
import { test, expect } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
let child: ChildProcess, origin: string, state: string, ticket: string;
let failures: string[] = [];
let bridge: WebSocket;
let requestIndex = 0;
async function applicationRpc(method: string, params: unknown) {
  if (!bridge) {
    const pair = JSON.parse(
      await readFile(resolve(state, "fixture-pair.json"), "utf8"),
    );
    bridge = new WebSocket(origin.replace("http:", "ws:") + "/v1/bridge", {
      headers: { Authorization: "Bearer " + pair.token },
    });
    await new Promise<void>((resolve, reject) => {
      bridge.once("open", () => resolve());
      bridge.once("error", reject);
    });
  }
  const id = String(++requestIndex);
  return new Promise<void>((resolve, reject) => {
    const listener = (raw: WebSocket.RawData) => {
      const message = JSON.parse(raw.toString());
      if (message.id !== id) return;
      bridge.off("message", listener);
      message.error ? reject(new Error(message.error.message)) : resolve();
    };
    bridge.on("message", listener);
    bridge.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
  });
}

test.describe.configure({ mode: "serial" });
test.beforeAll(async () => {
  await mkdir(resolve(root, ".local/browser-tests"), { recursive: true });
  state = await mkdtemp(resolve(root, ".local/browser-tests/state-"));
  const socket = createServer();
  await new Promise<void>((r) => socket.listen(0, "127.0.0.1", r));
  const address = socket.address() as { port: number };
  await new Promise<void>((r) => socket.close(() => r()));
  origin = "http://127.0.0.1:" + address.port;
  child = spawn(
    resolve(root, ".runtime/worker-venv/bin/python"),
    [
      "tests/fixtures/server.py",
      "--root",
      state,
      "--port",
      String(address.port),
      "--web-root",
      resolve(root, "web/dist"),
      "--mock-models",
    ],
    { cwd: root, stdio: "ignore" },
  );
  let ready = false;
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(origin + "/ready")).ok) {
        ready = true;
        break;
      }
    } catch {}
    await new Promise((r) => setTimeout(r, 50));
  }
  expect(ready).toBeTruthy();
  ticket = await readFile(resolve(state, "fixture-launch.txt"), "utf8");
});
test.afterAll(async () => {
  bridge?.close();
  if (child) {
    child.kill("SIGTERM");
    await new Promise((r) => {
      if (child.exitCode !== null) return r(null);
      child.once("exit", r);
    });
  }
  if (state) await rm(state, { recursive: true, force: true });
});
test("FPL shell, catalog editing, test history, files, bridge pairing and narrow layouts", async ({
  page,
}) => {
  page.on("pageerror", (e) => failures.push(e.message));
  page.on("response", (r) => {
    if (r.status() >= 400 && r.url().includes("/v1/"))
      failures.push(r.status() + " " + new URL(r.url()).pathname);
  });
  await page.goto(origin + "/#ticket=" + ticket);
  await expect(
    page.getByRole("heading", { name: "今天想从哪里开始？" }),
  ).toBeVisible();
  expect(page.url()).not.toContain("ticket=");
  await page.getByRole("button", { name: "深色", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.screenshot({
    path: resolve(root, ".local/browser-tests/home-dark.png"),
    fullPage: false,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "浅色", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.screenshot({
    path: resolve(root, ".local/browser-tests/home-light.png"),
    animations: "disabled",
  });
  await page.getByRole("button", { name: "深色", exact: true }).click();
  await page.getByRole("button", { name: "全部应用", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "应用", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("还没有安装应用", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "安装应用", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "安装应用" })).toBeVisible();
  await page.keyboard.press("Escape");
  const plugin = {
    id: "plugin-notes",
    name: "插件笔记",
    dashboard: {
      category: "knowledge",
      url: origin + "/health",
      package_name: "dsh-notes",
      version: "1.0.0",
      description: "通过 DSH 插件登记的应用",
      capabilities: ["笔记"],
      icon: "notebook",
    },
  };
  await applicationRpc("application.register", plugin);
  await expect(
    page.getByRole("button", { name: "打开插件笔记", exact: true }),
  ).toBeVisible();
  await page.getByLabel("搜索应用").fill("不存在");
  await expect(
    page.getByText("没有找到匹配的应用", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("搜索应用").fill("");
  await page.getByRole("button", { name: "打开插件笔记", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "打开应用", exact: true }),
  ).toHaveAttribute("href", origin + "/health");
  await page.goto(origin + "/");
  await page.screenshot({
    path: resolve(root, ".local/browser-tests/home-with-app.png"),
    animations: "disabled",
  });
  await page
    .getByRole("button", { name: "知识与表达，1 个应用", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "打开插件笔记", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "管理模型", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "模型与智能体" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "添加接入连接", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("连接名称").fill("独立测试连接");
  await dialog
    .getByLabel("服务地址", { exact: true })
    .fill("https://api.example.com/v1");
  await dialog.getByLabel("API Key", { exact: true }).fill("fixture-secret");
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.getByRole("tab", { name: "模型目录", exact: true }).click();
  await page.getByRole("button", { name: "添加模型", exact: true }).click();
  const model = page.getByRole("dialog");
  await model.getByLabel("模型标识").fill("fixture-chat");
  await model.getByLabel("显示名称").fill("独立对话模型");
  await model.getByRole("button", { name: "保存", exact: true }).click();
  await expect(model).toHaveCount(0);
  await expect(page.getByText("独立对话模型", { exact: true })).toBeVisible();
  await page.screenshot({
    path: resolve(root, ".local/browser-tests/catalog-dark.png"),
    fullPage: false,
    animations: "disabled",
  });
  // Use the catalog's real test action, then send a message through the production SSE adapter.
  const catalog = await page.request.get(origin + "/v1/model-providers");
  const id = (await catalog.json()).providers[0].models[0].id;
  await page.goto(origin + "/model-test?model=" + encodeURIComponent(id));
  await expect(page.locator("textarea").last()).toBeVisible();
  const composer = page.getByPlaceholder("输入要测试的问题");
  await composer.fill("验证独立服务");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByText("独立 Core 测试回复，已保留会话历史。", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText("独立 Core 测试回复，已保留会话历史。", { exact: true }),
  ).toBeVisible();
  await page.goto(origin + "/model-call-logs");
  await expect(
    page.getByRole("heading", { name: "模型调用日志" }),
  ).toBeVisible();
  await expect(
    page.getByText("fixture-chat", { exact: true }).first(),
  ).toBeVisible();
  await page.goto(origin + "/files");
  const upload = page.getByRole("button", { name: "上传文件", exact: true });
  const icon = await upload.locator("svg").boundingBox(),
    label = await upload.locator("span").boundingBox();
  expect(
    Math.abs(icon!.y + icon!.height / 2 - (label!.y + label!.height / 2)),
  ).toBeLessThan(1);
  const chooser = page.waitForEvent("filechooser");
  await upload.press("Enter");
  await (
    await chooser
  ).setFiles({
    name: "note.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("independent resource"),
  });
  await expect(page.getByText("note.txt", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "预览 note.txt", exact: true })
    .first()
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  for (const theme of ["light", "dark"]) {
    await page
      .getByRole("button", {
        name: theme === "light" ? "浅色" : "深色",
        exact: true,
      })
      .click();
    await page.screenshot({
      path: resolve(root, `.local/browser-tests/files-${theme}.png`),
      animations: "disabled",
    });
  }
  await page.goto(origin + "/apps");
  await applicationRpc("application.unregister", { id: "plugin-notes" });
  await expect(
    page.getByRole("button", { name: "打开插件笔记", exact: true }),
  ).toHaveCount(0);
  await applicationRpc("application.register", plugin);
  await expect(page.getByText("可打开", { exact: true })).toBeVisible();
  const skillZip = Buffer.from(
    zipSync({
      "SKILL.md": strToU8(
        "---\nname: browser-skill\ndescription: Browser skill\n---\nRead the reference.",
      ),
      "references/data.txt": strToU8("Complete attachment"),
    }),
  ).toString("base64");
  const configurations: any[] = [
    {
      kind: "tool-apis",
      id: "browser-api",
      name: "浏览器工具",
      config: {
        name: "浏览器工具",
        url: "http://127.0.0.1:19320/tool",
        method: "GET",
        auth_kind: "bearer",
        secret: "browser-hidden-secret",
        parameters_schema: { type: "object" },
      },
    },
    {
      kind: "mcp",
      id: "browser-mcp",
      name: "浏览器 MCP",
      config: {
        name: "浏览器 MCP",
        transport: "streamable_http",
        url: "http://127.0.0.1:19320/mcp",
        headers: { "X-Key": "browser-hidden-secret" },
      },
    },
    {
      kind: "skills",
      id: "browser-skill",
      name: "browser-skill",
      config: { name: "browser-skill", description: "Browser skill", tags: [] },
      archive_base64: skillZip,
    },
    {
      kind: "external-connections",
      id: "browser-feishu",
      name: "浏览器飞书",
      config: {
        name: "浏览器飞书",
        provider: "feishu",
        app_id: "cli_fixture",
        app_secret: "browser-hidden-secret",
      },
    },
  ].map((row) => ({ ...row, schema_version: 1, revision: "1" }));
  const imported = new Map<string, any>(),
    receipts = new Map<string, any>();
  bridge.on("message", (raw) => {
    const request = JSON.parse(raw.toString());
    if (!request.method?.startsWith("integrations.")) return;
    const p = request.params;
    let result: any;
    if (request.method === "integrations.list")
      result = configurations.map((r) => ({
        id: r.id,
        kind: r.kind,
        name: r.name,
        revision: r.revision,
        enabled: true,
        secret_fields: ["credentials"],
      }));
    if (request.method === "integrations.export")
      result = configurations.find((r) => r.kind === p.kind && r.id === p.id);
    if (request.method === "integrations.import") {
      imported.set(p.target_id, {
        ...p.item,
        config: { ...p.item.config, enabled: false },
      });
      result = {
        id: p.target_id,
        kind: p.item.kind,
        name: p.item.name,
        revision: "0",
      };
      receipts.set(p.request_id, { status: "completed", result });
    }
    if (request.method === "integrations.receipt")
      result = receipts.get(p.request_id) || { status: "not-found" };
    bridge.send(JSON.stringify({ jsonrpc: "2.0", id: request.id, result }));
  });
  await applicationRpc("integrations.publish", {
    adapters: [
      {
        id: "ui-source",
        application_id: "plugin-notes",
        name: "浏览器集成适配器",
        kinds: ["tool-apis", "mcp", "skills", "external-connections"],
        can_import: true,
        can_export: true,
      },
    ],
  });
  await page.goto(origin + "/integrations");
  await expect(
    page.getByRole("heading", { name: "集成中心", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "从 DSH 导入", exact: true }).click();
  let transfer = page.getByRole("dialog");
  await expect(transfer.getByRole("checkbox")).toHaveCount(4);
  for (const checkbox of await transfer.getByRole("checkbox").all())
    await checkbox.check();
  await transfer.getByRole("button", { name: "预览导入", exact: true }).click();
  await expect(transfer.getByText("确认以下变更后导入。")).toBeVisible();
  expect(await transfer.innerText()).not.toContain("browser-hidden-secret");
  await transfer.getByRole("button", { name: "确认导入", exact: true }).click();
  await expect(
    transfer.getByText("导入已完成，配置保存为停用副本。"),
  ).toBeVisible();
  await transfer.getByRole("button", { name: "完成", exact: true }).click();
  await expect(
    page.getByText("浏览器工具", { exact: true }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "导入到 DSH", exact: true }).click();
  transfer = page.getByRole("dialog");
  await expect(transfer.getByRole("checkbox")).toHaveCount(4);
  for (const checkbox of await transfer.getByRole("checkbox").all())
    await checkbox.check();
  await transfer.getByRole("button", { name: "预览导入", exact: true }).click();
  await transfer.getByRole("button", { name: "确认导入", exact: true }).click();
  await expect(
    transfer.getByText("导入已完成，配置保存为停用副本。"),
  ).toBeVisible();
  expect(imported.size).toBe(4);
  expect(
    [...imported.values()].every((row) => row.config.enabled === false),
  ).toBeTruthy();
  await transfer.getByRole("button", { name: "完成", exact: true }).click();
  for (const tab of ["工具 API", "MCP", "Skill", "外部连接"]) {
    await page.getByRole("tab", { name: tab, exact: true }).click();
    await expect(page.locator("[role=tabpanel]:visible")).toHaveCount(1);
  }
  await expect(
    page.getByRole("button", { name: "连接 DSH", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("DeepSeek Harness", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "连接", exact: true }),
  ).toHaveCount(0);
  for (const theme of ["浅色", "深色"]) {
    await page.getByRole("button", { name: theme, exact: true }).click();
    for (const width of [1440, 860, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.evaluate(
        () =>
          new Promise<void>((r) =>
            requestAnimationFrame(() => requestAnimationFrame(() => r())),
          ),
      );
      await expect(page.locator(".studio-shell")).toHaveCount(1);
      await page.screenshot({
        path: resolve(
          root,
          `.local/browser-tests/integrations-${theme}-${width}.png`,
        ),
        animations: "disabled",
      });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBeTruthy();
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  bridge.close();
  await page.goto(origin + "/apps");
  await expect(page.getByText("DSH 未连接", { exact: true })).toBeVisible();
  await page.goto(origin + "/settings");
  await page.getByRole("button", { name: "连接 DSH", exact: true }).click();
  await page.getByRole("button", { name: "创建配对凭据", exact: true }).click();
  await expect(page.getByLabel("配对凭据")).not.toHaveValue("");
  await page.getByRole("button", { name: "完成", exact: true }).click();
  await page.goto(origin + "/");
  await page.setViewportSize({ width: 860, height: 900 });
  await expect(
    page.getByRole("heading", { name: "今天想从哪里开始？" }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(root, ".local/browser-tests/home-side-by-side.png"),
    fullPage: false,
    animations: "disabled",
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "今天想从哪里开始？" }),
  ).toBeVisible();
  await expect(page.locator(".studio-shell")).toHaveCount(1);
  await page.screenshot({
    path: resolve(root, ".local/browser-tests/home-narrow.png"),
    fullPage: false,
    animations: "disabled",
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  expect(failures).toEqual([]);
});
