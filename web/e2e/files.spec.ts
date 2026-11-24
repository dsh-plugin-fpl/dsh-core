import { zipSync, strToU8 } from "fflate";
import { test, expect } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
let child: ChildProcess, origin: string, state: string;
test.beforeAll(async () => {
  await mkdir(resolve(root, ".local/files-browser"), { recursive: true });
  await mkdir(resolve(root, ".local/files-browser/local-input/sub"), {
    recursive: true,
  });
  await writeFile(
    resolve(root, ".local/files-browser/local-input/sub/local.txt"),
    "local directory body",
  );
  state = await mkdtemp(resolve(root, ".local/files-browser/state-"));
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
    {
      cwd: root,
      stdio: "ignore",
      env: {
        ...process.env,
        DSH_FPL_STORAGE_CONFIG: resolve(state, "locator.json"),
        DSH_FPL_STORAGE_CONTROL: resolve(state, "control"),
      },
    },
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
test("原版文件列表、网格、预览、管理操作与宽窄屏", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const ticket = await readFile(resolve(state, "fixture-launch.txt"), "utf8");
  await page.goto(origin + "/#ticket=" + ticket);
  await expect(
    page.getByRole("heading", { name: "今天想从哪里开始？" }),
  ).toBeVisible();
  const csrf = (await page.context().cookies()).find(
    (cookie) => cookie.name === "core_csrf",
  )!.value;
  const headers = { Origin: origin, "X-Core-CSRF": csrf };
  for (const name of [
    "一百块的吻 | 制作方案",
    "ZONOS3–冈牧音色与情绪对照–20261119",
    "人设",
    "LongUnbrokenFolderName".repeat(6),
  ]) {
    const response = await page.request.post(
      origin + "/v1/file-library/folders",
      { headers, data: { name } },
    );
    expect(response.ok()).toBeTruthy();
  }
  await page.goto(origin + "/files");
  await expect(
    page.getByRole("heading", { name: "文件管理", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "列表视图", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  const chooser = page.waitForEvent("filechooser");
  await page
    .getByRole("button", { name: "上传文件", exact: true })
    .press("Enter");
  await (
    await chooser
  ).setFiles([
    {
      name: "说明.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("真实文件内容 <script>不会执行</script>"),
    },
    {
      name: "示意.svg",
      mimeType: "image/svg+xml",
      buffer: Buffer.from(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80"><rect width="100" height="80" fill="#e4704b"/></svg>',
      ),
    },
  ]);
  await expect(
    page.getByRole("button", { name: "预览 说明.txt", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("4 个文件夹 · 2 个文件", { exact: true }),
  ).toBeVisible();
  for (const width of [1920, 800, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const theme of ["浅色", "深色"]) {
      await page.getByRole("button", { name: theme, exact: true }).click();
      for (const view of ["列表视图", "网格视图"]) {
        await page.getByRole("button", { name: view, exact: true }).click();
        await expect(page.locator(".file-folder-item")).toHaveCount(4);
        expect(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        ).toBeTruthy();
        for (const row of await page.locator(".file-items article").all()) {
          expect(
            await row.evaluate(
              (element) => element.scrollWidth <= element.clientWidth + 1,
            ),
          ).toBeTruthy();
        }
        expect(
          (await page.locator(".file-folder-item").first().boundingBox())!
            .height,
        ).toBeLessThan(100);
        await page.screenshot({
          path: resolve(
            root,
            `.local/files-browser/${width}-${theme}-${view}.png`,
          ),
          animations: "disabled",
          fullPage: true,
        });
      }
    }
  }
  await page
    .getByRole("button", { name: "预览 说明.txt", exact: true })
    .click();
  await expect(page.getByRole("dialog").locator("pre")).toHaveText(
    "真实文件内容 <script>不会执行</script>",
  );
  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: "预览 示意.svg", exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .getByRole("dialog")
        .locator("img")
        .evaluate(
          (image: HTMLImageElement) => image.complete && image.naturalWidth > 0,
        ),
    )
    .toBeTruthy();
  await page.keyboard.press("Escape");
  await page
    .getByRole("textbox", { name: "搜索全部文件", exact: true })
    .fill("人设");
  await expect(page.locator(".file-folder-item")).toHaveCount(1);
  await page
    .getByRole("textbox", { name: "搜索全部文件", exact: true })
    .fill("");
  await expect(page.locator(".file-folder-item")).toHaveCount(4);
  await page.getByRole("combobox", { name: "文件类型", exact: true }).click();
  await page.getByRole("option", { name: "图片", exact: true }).click();
  await expect(page.locator(".file-folder-item")).toHaveCount(0);
  await expect(page.locator(".file-asset-item")).toHaveCount(1);
  await page.getByRole("combobox", { name: "文件类型", exact: true }).click();
  await page.getByRole("option", { name: "全部类型", exact: true }).click();
  await page.getByRole("button", { name: "新建文件夹", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByLabel("名称", { exact: true })
    .fill("验收目录");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "打开文件夹 验收目录", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "说明.txt 的更多操作", exact: true })
    .click();
  await page.getByRole("menuitem", { name: "重命名", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByLabel("名称", { exact: true })
    .fill("重命名.txt");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "预览 重命名.txt", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "重命名.txt 的更多操作", exact: true })
    .click();
  await page.getByRole("menuitem", { name: "移动到…", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("combobox", { name: "目标文件夹", exact: true })
    .click();
  await page.getByRole("option", { name: "验收目录", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "移动", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "预览 重命名.txt", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "打开文件夹 验收目录", exact: true })
    .press("Enter");
  await expect(
    page.getByRole("button", { name: "预览 重命名.txt", exact: true }),
  ).toBeVisible();
  const nestedUpload = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "上传文件", exact: true }).click();
  await (
    await nestedUpload
  ).setFiles({
    name: "子目录.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("nested"),
  });
  await expect(
    page.getByText("0 个文件夹 · 2 个文件", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "更多操作", exact: true }).click();
  const download = page.waitForEvent("download");
  await page
    .getByRole("menuitem", { name: "导出当前文件夹 ZIP", exact: true })
    .click();
  expect((await download).suggestedFilename()).toBe("files.zip");
  for (const name of ["重命名.txt", "子目录.txt"]) {
    await page
      .getByRole("button", { name: name + " 的更多操作", exact: true })
      .click();
    await page
      .getByRole("menuitem", { name: "从文件管理移除", exact: true })
      .click();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "确认", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "预览 " + name, exact: true }),
    ).toHaveCount(0);
  }
  await page.getByRole("button", { name: "全部文件", exact: true }).click();
  await page
    .getByRole("button", { name: "验收目录 的更多操作", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "删除空文件夹", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "确认", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "打开文件夹 验收目录", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "更多操作", exact: true }).click();
  const zipChooser = page.waitForEvent("filechooser");
  await page
    .getByRole("menuitem", { name: "导入 ZIP 归档", exact: true })
    .click();
  await (
    await zipChooser
  ).setFiles({
    name: "fixture.zip",
    mimeType: "application/zip",
    buffer: Buffer.from(zipSync({ "ZIP目录/hello.txt": strToU8("zip body") })),
  });
  await expect(
    page.getByRole("button", { name: "打开文件夹 ZIP目录", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "打开文件夹 ZIP目录", exact: true })
    .click();
  await page
    .getByRole("button", { name: "预览 hello.txt", exact: true })
    .click();
  await expect(page.getByRole("dialog").locator("pre")).toHaveText("zip body");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "全部文件", exact: true }).click();
  await page.getByRole("button", { name: "更多操作", exact: true }).click();
  const directoryChooser = page.waitForEvent("filechooser");
  await page
    .getByRole("menuitem", { name: "导入本地文件夹", exact: true })
    .click();
  await (
    await directoryChooser
  ).setFiles(resolve(root, ".local/files-browser/local-input"));
  await expect(
    page.getByRole("button", { name: "打开文件夹 local-input", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "打开文件夹 local-input", exact: true })
    .click();
  await page
    .getByRole("button", { name: "打开文件夹 sub", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "预览 local.txt", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "全部文件", exact: true }).click();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "网格视图", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(errors).toEqual([]);
});
