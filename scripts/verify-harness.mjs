/** Install the current tarball into an isolated Harness; never touch the desktop profile. */
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:net";
import assert from "node:assert/strict";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const temporary = resolve(root, ".local/harness-independent"),
  home = resolve(root, ".runtime/harness-independent");
await mkdir(temporary, { recursive: true });
const profileName = "core-independent-" + Date.now();
const profile = resolve(home, "profiles", profileName);
await mkdir(profile, { recursive: true });
const pkg = JSON.parse(await readFile(resolve(root, "package.json"), "utf8"));
const exampleDirectory = resolve(root, "examples/dashboard-app");
const examplePackage = JSON.parse(
  await readFile(resolve(exampleDirectory, "package.json"), "utf8"),
);
const examplePack = spawnSync(
  "pnpm",
  [
    "--dir",
    exampleDirectory,
    "pack",
    "--pack-destination",
    resolve(root, ".runtime/packages"),
  ],
  { cwd: root, stdio: "inherit" },
);
if (examplePack.status !== 0) process.exit(examplePack.status ?? 1);
const exampleTarball = resolve(
  root,
  ".runtime/packages/" +
    examplePackage.name.replace(/^@/, "").replace("/", "-") +
    "-" +
    examplePackage.version +
    ".tgz",
);
const tarball = resolve(
  root,
  ".runtime/packages/dsh-plugin-fpl-dsh-core-" + pkg.version + ".tgz",
);
await writeFile(
  resolve(profile, "package.json"),
  JSON.stringify(
    {
      name: "fpl-core-isolated-profile",
      private: true,
      dependencies: {
        "@dsh-plugin-fpl/dsh-core": "file:" + tarball,
        "@deepseek-ai/dsh-mcp-client": "0.2.1-rc.2",
        [examplePackage.name]: "file:" + exampleTarball,
        "fpl-core-isolated-smoke":
          "file:" + resolve(root, "tests/fixtures/harness-smoke"),
      },
      dsh: {
        profile: {
          bundles: [
            "@deepseek-ai/dsh-base",
            "@deepseek-ai/dsh-web-app",
            "@dsh-plugin-fpl/dsh-core",
            "fpl-core-isolated-smoke",
            examplePackage.name,
          ],
        },
      },
    },
    null,
    2,
  ),
);
const installed = spawnSync("pnpm", ["install", "--ignore-scripts"], {
  cwd: profile,
  stdio: "inherit",
});
if (installed.status !== 0) process.exit(installed.status ?? 1);
async function freePort() {
  const socket = createServer();
  await new Promise((r) => socket.listen(0, "127.0.0.1", r));
  const port = socket.address().port;
  await new Promise((r) => socket.close(r));
  return port;
}
const port = await freePort(),
  webPort = await freePort(),
  origin = "http://127.0.0.1:" + port;
const state = resolve(temporary, "state-" + Date.now()),
  report = resolve(temporary, "report-" + Date.now() + ".json");
await writeFile(
  resolve(profile, "cordis.patch.yml"),
  "- id: dsh-core\n  config:\n    endpoint: " + origin + "\n",
);
const skillRoot = resolve(temporary, "skill-" + Date.now());
await mkdir(resolve(skillRoot, "references"), { recursive: true });
await writeFile(
  resolve(skillRoot, "SKILL.md"),
  "---\nname: harness-packaged-skill\ndescription: Harness packaged skill\n---\nRead the reference.",
);
await writeFile(
  resolve(skillRoot, "references/data.txt"),
  "Harness attachment",
);
// Register a real native stdio server through the isolated profile, never the desktop profile.
const patch = resolve(profile, "cordis.patch.yml");
await writeFile(
  patch,
  (await readFile(patch, "utf8")) +
    '- insert:\n    - id: native-fixture-mcp\n      name: "@deepseek-ai/dsh-mcp-client"\n      config:\n        serverName: native_fixture\n        transport: stdio\n        command: ' +
    JSON.stringify(resolve(root, ".runtime/worker-venv/bin/python")) +
    "\n        args: [" +
    JSON.stringify(resolve(root, "python/tests/fixtures/mcp_echo.py")) +
    "]\n        env:\n          FIXTURE_SECRET: harness-mcp-secret\n        cwd: " +
    JSON.stringify(temporary) +
    "\n",
);
let core, harness;
const logs = [];
async function stop(child) {
  if (!child || child.exitCode !== null) return;
  child.kill("SIGTERM");
  await Promise.race([
    new Promise((r) => child.once("exit", r)),
    new Promise((r) => setTimeout(r, 6000)),
  ]);
  if (child.exitCode === null) child.kill("SIGKILL");
}
try {
  core = spawn(
    resolve(root, ".runtime/worker-venv/bin/python"),
    ["tests/fixtures/server.py", "--root", state, "--port", String(port)],
    { cwd: root, stdio: ["ignore", "ignore", "pipe"] },
  );
  core.stderr.on("data", (x) => logs.push(x.toString()));
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
  assert.ok(ready, "Fixture Core not ready");
  harness = spawn(
    "dsh",
    ["--profile", profileName, "--no-open", "--port", String(webPort)],
    {
      cwd: root,
      env: {
        ...process.env,
        DSH_HOME: home,
        CORE_FIXTURE_PAIR_FILE: resolve(state, "fixture-pair.json"),
        CORE_FIXTURE_REPORT: report,
        CORE_FIXTURE_APP_URL: "http://127.0.0.1:" + webPort + "/",
        CORE_FIXTURE_SKILL_ROOT: skillRoot,
        CORE_FIXTURE_API_URL: origin + "/health",
        CORE_FIXTURE_INTEGRATION_REPORT: report + ".integrations",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  harness.stdout.on("data", (x) => logs.push(x.toString()));
  harness.stderr.on("data", (x) => logs.push(x.toString()));
  let result;
  for (let i = 0; i < 300; i++) {
    try {
      result = JSON.parse(await readFile(report, "utf8"));
      break;
    } catch {}
    if (harness.exitCode !== null) throw new Error("Isolated Harness exited");
    await new Promise((r) => setTimeout(r, 100));
  }
  assert.equal(
    result?.status,
    "completed",
    result?.message || "Isolated consumer timed out",
  );
  const ticket = await readFile(resolve(state, "fixture-launch.txt"), "utf8");
  const login = await fetch(origin + "/v1/session", {
    method: "POST",
    headers: { Origin: origin, "Content-Type": "application/json" },
    body: JSON.stringify({ ticket }),
  });
  assert.ok(login.ok);
  const cookie = login.headers
    .getSetCookie()
    .map((x) => x.split(";")[0])
    .join("; ");
  const catalog = await (
    await fetch(origin + "/v1/applications", { headers: { Cookie: cookie } })
  ).json();
  const registered = catalog.items.find(
    (app) => app.package_name === "fpl-core-isolated-smoke",
  );
  assert.equal(registered?.status, "ready");
  const example = catalog.items.find(
    (app) => app.package_name === examplePackage.name,
  );
  assert.equal(
    example?.status,
    "ready",
    "Installed example missing from dashboard",
  );
  const appPage = await fetch(example.url);
  assert.ok(appPage.ok);
  assert.ok((await appPage.text()).includes("插件应用示例"));
  const csrf = (await login.json()).csrf;
  async function integrationApi(path, body) {
    const response = await fetch(origin + path, {
      method: body ? "POST" : "GET",
      headers: {
        Cookie: cookie,
        Origin: origin,
        "X-Core-CSRF": csrf,
        "Content-Type": "application/json",
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    const value = await response.json();
    assert.ok(response.ok, "Integration API failed: " + response.status);
    return value;
  }
  const adapters = (await integrationApi("/v1/integrations/dsh-adapters"))
    .items;
  const imported = [];
  for (const adapterId of ["native-dsh", "harness-integration-source"]) {
    const adapter = adapters.find((a) => a.id === adapterId);
    assert.ok(adapter, "Missing " + adapterId);
    let candidates = [];
    for (let attempt = 0; attempt < 50; attempt++) {
      candidates = (
        await integrationApi(
          "/v1/integrations/transfer-candidates?" +
            new URLSearchParams({
              direction: "from-dsh",
              bridge_id: adapter.bridge_id,
              adapter_id: adapter.id,
            }),
        )
      ).items;
      if (
        adapterId !== "native-dsh" ||
        candidates.some((c) => c.kind === "mcp" && c.name === "native_fixture")
      )
        break;
      await new Promise((r) => setTimeout(r, 100));
    }
    const items = candidates
      .filter((c) =>
        adapterId === "native-dsh"
          ? (c.kind === "mcp" && c.name === "native_fixture") ||
            (c.kind === "skills" && c.name === "harness-packaged-skill")
          : true,
      )
      .map(({ kind, id }) => ({ kind, id }));
    assert.equal(
      items.length,
      2,
      "Native discovery metadata: " +
        JSON.stringify({
          candidates,
          settings: result.settings_metadata,
          inventory: result.inventory_metadata,
        }),
    );
    const plan = await integrationApi("/v1/integrations/transfers/preview", {
      direction: "from-dsh",
      bridge_id: adapter.bridge_id,
      adapter_id: adapter.id,
      items,
    });
    assert.ok(!JSON.stringify(plan).includes("harness-mcp-secret"));
    const applied = await integrationApi(
      "/v1/integrations/transfers/" + plan.id + "/apply",
      { choices: plan.rows.map((r) => ({ key: r.key, action: "create" })) },
    );
    assert.equal(applied.status, "completed");
    imported.push(
      ...applied.rows.map((r) => ({ kind: r.kind, id: r.result.id })),
    );
  }
  const managed = adapters.find((a) => a.id === "core-managed");
  assert.ok(managed);
  const preview = await integrationApi("/v1/integrations/transfers/preview", {
    direction: "to-dsh",
    bridge_id: managed.bridge_id,
    adapter_id: managed.id,
    items: imported,
  });
  assert.equal(
    (
      await integrationApi(
        "/v1/integrations/transfers/" + preview.id + "/apply",
        {
          choices: preview.rows.map((r) => ({ key: r.key, action: "create" })),
        },
      )
    ).status,
    "completed",
  );
  let integrations;
  for (let i = 0; i < 200; i++) {
    try {
      integrations = JSON.parse(
        await readFile(report + ".integrations", "utf8"),
      );
      break;
    } catch {}
    await new Promise((r) => setTimeout(r, 100));
  }
  assert.equal(
    integrations?.status,
    "completed",
    integrations?.message || "Activation report timed out",
  );
  await stop(harness);
  assert.ok(
    (await fetch(origin + "/ready")).ok,
    "Closing Harness stopped Core",
  );
  await writeFile(
    resolve(temporary, "latest.json"),
    JSON.stringify(
      {
        ...result,
        integrations,
        dashboard_registration: true,
        example_application_page: true,
        independent_core_survived: true,
        tarball,
      },
      null,
      2,
    ),
  );
  console.log(
    "Isolated Harness tarball: native model, resource and independent lifecycle passed",
  );
} finally {
  await stop(harness);
  await stop(core);
  await writeFile(
    resolve(temporary, "harness.log"),
    logs.join("").replace(/([?&]token=)[^\s&]+/g, "$1[redacted]"),
    {
      mode: 0o600,
    },
  );
}
