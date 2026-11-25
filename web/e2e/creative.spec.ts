import { zipSync, strToU8 } from "fflate";
import { test, expect } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const source = process.env.FPL_SOURCE_REPOSITORY;
let core: ChildProcess,
  remote: ChildProcess | undefined,
  origin: string,
  site: string,
  state: string;
async function port() {
  const socket = createServer();
  await new Promise<void>((r) => socket.listen(0, "127.0.0.1", r));
  const value = (socket.address() as { port: number }).port;
  await new Promise<void>((r) => socket.close(() => r()));
  return value;
}
test.beforeAll(async () => {
  await mkdir(resolve(root, ".local/creative-browser"), { recursive: true });
  state = await mkdtemp(resolve(root, ".local/creative-browser/state-"));
  const corePort = await port();
  origin = `http://127.0.0.1:${corePort}`;
  core = spawn(
    resolve(root, ".runtime/worker-venv/bin/python"),
    [
      "tests/fixtures/server.py",
      "--root",
      resolve(state, "core"),
      "--port",
      String(corePort),
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
  if (source) {
    const sourcePort = await port();
    site = `http://127.0.0.1:${sourcePort}`;
    remote = spawn(
      resolve(source, ".venv/bin/python"),
      [
        "tests/fixtures/creative_server.py",
        "--root",
        resolve(state, "website"),
        "--port",
        String(sourcePort),
      ],
      { cwd: source, stdio: "ignore" },
    );
    await expect
      .poll(
        async () => {
          try {
            return (await fetch(site + "/v1/auth/me")).status === 401;
          } catch {
            return false;
          }
        },
        { timeout: 30000 },
      )
      .toBeTruthy();
  }
});
test.afterAll(async () => {
  for (const process of [core, remote])
    if (process && process.exitCode === null) {
      process.kill("SIGTERM");
      await new Promise((r) => process.once("exit", r));
    }
  if (state) await rm(state, { recursive: true, force: true });
});
test("直接访问以本机所有者进入，明暗及窄屏网站登录入口", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(origin);
  await expect(
    page.getByRole("link", { name: "本机所有者", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "登录 FPL", exact: true }).click();
  for (const width of [1440, 800, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const theme of ["浅色", "深色"]) {
      await page.getByRole("button", { name: theme, exact: true }).click();
      await expect(page.getByLabel("FPL 网站地址")).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBeTruthy();
      await page.screenshot({
        path: resolve(
          root,
          `.local/creative-browser/account-${width}-${theme}.png`,
        ),
      });
    }
  }
  expect(errors).toEqual([]);
});
test("真实网站登录、发布、下载安装到本机以及退出后本地仍可用", async ({
  page,
}) => {
  test.skip(
    !source,
    "Set FPL_SOURCE_REPOSITORY for the real website HTTP journey",
  );
  await page.goto(origin + "/fpl-account");
  await page.getByLabel("FPL 网站地址").fill(site);
  await page.getByLabel("网站账号", { exact: true }).fill("fixture-alice");
  await page.getByLabel("网站密码").fill("fixture-password-123");
  await page.getByRole("button", { name: "登录 FPL", exact: true }).click();
  await expect(
    page.getByText("已连接 " + site + " · fixture-alice"),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "本机所有者", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "打开创意工坊" }).click();
  await page.getByRole("button", { name: "＋ 发布创作" }).click();
  await page.getByRole("button", { name: /工具 API.*分享 HTTP/ }).click();
  // The real editor is now bound to a website draft, while installation uses local APIs.
  await expect(
    page.getByText("工具 API", { exact: true }).first(),
  ).toBeVisible();
  const archive = Array.from(
    zipSync({
      "SKILL.md": strToU8(
        "---\nname: browser-skill\ndescription: Browser fixture\n---\n# Fixture",
      ),
      "assets/reference.txt": strToU8("Complete browser attachment"),
    }),
  );
  const result = await page.evaluate(
    async ({ archive }) => {
      const csrf = decodeURIComponent(
        document.cookie
          .split("; ")
          .find((v) => v.startsWith("core_csrf="))!
          .split("=")[1],
      );
      async function api(path: string, body?: unknown) {
        const response = await fetch(
          path,
          body
            ? {
                method: "POST",
                headers: {
                  "Content-Type": "application/json",
                  "X-Core-CSRF": csrf,
                },
                body: JSON.stringify(body),
              }
            : {},
        );
        if (!response.ok) throw Error(await response.text());
        return response.json();
      }
      let draft = await api("/v1/creative-workshop/items", {
        kind: "tool_api",
        title: "Browser fixture API",
        summary: "A public synthetic capability",
        manifest: { url: "https://fixture.example/api", method: "GET" },
      });
      draft = await api(`/v1/creative-workshop/items/${draft.id}/publish`, {
        expected_revision: draft.revision,
      });
      const components = [draft.current_release_id];
      for (const manifest of [
        { transport: "streamable_http", url: "https://fixture.example/mcp" },
        { transport: "stdio", command_hint: "Install the fixture MCP" },
      ]) {
        let mcp = await api("/v1/creative-workshop/items", {
          kind: "mcp",
          title:
            manifest.transport === "stdio"
              ? "Browser stdio"
              : "Browser HTTP MCP",
          summary: "Synthetic MCP",
          manifest,
        });
        mcp = await api(`/v1/creative-workshop/items/${mcp.id}/publish`, {
          expected_revision: mcp.revision,
        });
        components.push(mcp.current_release_id);
      }
      let skill = await api("/v1/creative-workshop/items", {
        kind: "skill",
        title: "Browser Skill",
        summary: "Synthetic Skill",
      });
      const upload = new FormData();
      upload.set("expected_revision", String(skill.revision));
      upload.set(
        "file",
        new Blob([new Uint8Array(archive)], { type: "application/zip" }),
        "fixture.zip",
      );
      const uploaded = await fetch(
        `/v1/creative-workshop/items/${skill.id}/skill-archive`,
        { method: "POST", headers: { "X-Core-CSRF": csrf }, body: upload },
      );
      if (!uploaded.ok) throw Error(await uploaded.text());
      skill = await uploaded.json();
      skill = await api(`/v1/creative-workshop/items/${skill.id}/publish`, {
        expected_revision: skill.revision,
      });
      components.push(skill.current_release_id);
      let bundle = await api("/v1/creative-workshop/items", {
        kind: "bundle",
        title: "Browser fixture bundle",
        summary: "Four capabilities",
        components,
      });
      bundle = await api(`/v1/creative-workshop/items/${bundle.id}/publish`, {
        expected_revision: bundle.revision,
      });
      return bundle;
    },
    { archive },
  );
  await page.goto(
    origin + "/creative-workshop?mode=creative-workshop-detail&id=" + result.id,
  );
  await page.getByRole("button", { name: "检查安装依赖" }).click();
  await page.getByRole("button", { name: "确认安装到本机" }).click();
  await expect(page.getByText("安装完成", { exact: true })).toBeVisible();
  const installed = await page.evaluate(async () =>
    (await fetch("/v1/integrations/tool-apis")).json(),
  );
  expect(installed.items).toHaveLength(1);
  expect(installed.items[0].enabled).toBe(false);
  const skills = await page.evaluate(async () =>
    (await fetch("/v1/integrations/skills")).json(),
  );
  expect(skills.items).toHaveLength(1);
  expect(skills.items[0].enabled).toBe(false);
  const skillResponse = await page.request.get(
    origin +
      `/v1/integrations/skills/${skills.items[0].id}/versions/v1/content`,
  );
  expect(skillResponse.ok()).toBeTruthy();
  const { unzipSync } = await import("fflate");
  const extracted = unzipSync(new Uint8Array(await skillResponse.body()));
  expect(new TextDecoder().decode(extracted["assets/reference.txt"])).toBe(
    "Complete browser attachment",
  );
  await page.screenshot({
    path: resolve(root, ".local/creative-browser/installed.png"),
  });
  await page.goto(origin + "/fpl-account");
  await page.getByRole("button", { name: "退出网站账号" }).click();
  await expect(page.getByLabel("网站密码")).toBeVisible();
  await page.goto(origin + "/integrations");
  await expect(
    page.getByRole("link", { name: "本机所有者", exact: true }),
  ).toBeVisible();
});
