import test from "node:test";
import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { resolve } from "node:path";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { createServer as httpServer } from "node:http";
import { zipSync, strToU8 } from "fflate";
import { Context } from "@deepseek-ai/cordis";
import { DshCore } from "../lib/index.js";
const base = resolve(".local/integration-network");
await mkdir(base, { recursive: true });
async function fixture(t) {
  const root = await mkdtemp(base + "/fixture-");
  const reservation = createServer();
  await new Promise((r) => reservation.listen(0, "127.0.0.1", r));
  const port = reservation.address().port;
  await new Promise((r) => reservation.close(r));
  const endpoint = "http://127.0.0.1:" + port;
  const child = spawn(
    resolve(".runtime/worker-venv/bin/python"),
    ["tests/fixtures/server.py", "--root", root, "--port", String(port)],
    { stdio: "ignore" },
  );
  t.after(async () => {
    child.kill("SIGTERM");
    await new Promise((r) => {
      if (child.exitCode !== null) return r();
      child.once("exit", r);
    });
    await rm(root, { recursive: true, force: true });
  });
  let ready = false;
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(endpoint + "/ready")).ok) {
        ready = true;
        break;
      }
    } catch {}
    await new Promise((r) => setTimeout(r, 50));
  }
  assert.ok(ready);
  const pair = JSON.parse(await readFile(root + "/fixture-pair.json", "utf8"));
  const keyStore = new Map([["DSH_CORE_BRIDGE", pair.token]]);
  const tools = new Map(),
    skills = new Map();
  const ctx = new Context();
  ctx.provide("credentials", {
    resolve: async (key) =>
      keyStore.has(key) ? { value: keyStore.get(key) } : undefined,
    set: async (key, value) => {
      keyStore.set(key, value);
    },
  });
  ctx.provide("typert", { register: () => () => {} });
  ctx.provide("profileContext", {
    dir: root + "/host-profile",
    home: root + "/host-home",
    cwd: root,
    name: "fixture",
  });
  ctx.provide("tools", {
    register: (tool) => {
      tools.set(tool.name, tool);
      return () => tools.delete(tool.name);
    },
  });
  ctx.provide("skills", {
    list: async () => [...skills.values()],
    get: async (id) => skills.get(id),
    register: (skill) => {
      skills.set(skill.name, skill);
      return () => skills.delete(skill.name);
    },
  });
  let service;
  const fiber = ctx.plugin({
    inject: ["credentials", "typert"],
    apply(scope) {
      service = new DshCore(scope, { endpoint });
    },
  });
  await fiber;
  t.after(() => fiber.dispose());
  const ticket = await readFile(root + "/fixture-launch.txt", "utf8");
  const login = await fetch(endpoint + "/v1/session", {
    method: "POST",
    headers: { Origin: endpoint, "Content-Type": "application/json" },
    body: JSON.stringify({ ticket }),
  });
  assert.ok(login.ok);
  const cookie = login.headers
    .getSetCookie()
    .map((x) => x.split(";")[0])
    .join("; ");
  const csrf = (await login.json()).csrf;
  async function api(path, body, method) {
    const response = await fetch(endpoint + path, {
      method: method || (body ? "POST" : "GET"),
      headers: {
        Cookie: cookie,
        Origin: endpoint,
        "X-Core-CSRF": csrf,
        "Content-Type": "application/json",
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    const value = await response.json();
    assert.ok(response.ok, JSON.stringify(value));
    return value;
  }
  await service.capabilities();
  return { service, ctx, root, endpoint, api, tools, skills, fiber };
}
function sampleSkill() {
  return Buffer.from(
    zipSync(
      {
        "SKILL.md": strToU8(
          "---\nname: imported-test-skill\ndescription: Imported instructions\n---\n# Instructions\nUse the attached reference.",
        ),
        "references/reference.txt": strToU8("Roundtrip attachment"),
      },
      { mtime: new Date("1980-01-01") },
    ),
  ).toString("base64");
}

test("bidirectional four-kind imports, credentials, disabled copies, tools and complete skills", async (t) => {
  const { service, api, tools, skills, root } = await fixture(t);
  let calls = 0;
  const server = httpServer(async (request, response) => {
    calls++;
    assert.equal(request.headers.authorization, "Bearer fixture-wire-secret");
    response.setHeader("Content-Type", "application/json");
    response.end(
      JSON.stringify({ result: "Success", echo: "fixture-wire-secret" }),
    );
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  t.after(() => new Promise((r) => server.close(r)));
  const url = "http://127.0.0.1:" + server.address().port;
  const records = [
    {
      schema_version: 1,
      id: "tool",
      kind: "tool-apis",
      name: "Imported API",
      revision: "1",
      config: {
        name: "Imported API",
        method: "POST",
        url,
        auth_kind: "bearer",
        secret: "fixture-wire-secret",
        parameters_schema: {
          type: "object",
          properties: { message: { type: "string" } },
          required: ["message"],
        },
        body_template: { message: "{{message}}" },
        query_template: {},
        enabled: true,
      },
    },
    {
      schema_version: 1,
      id: "mcp",
      kind: "mcp",
      name: "Imported MCP",
      revision: "1",
      config: {
        name: "Imported MCP",
        transport: "streamable_http",
        url: url + "/mcp",
        headers: { "X-Key": "mcp-wire-secret" },
        cwd: root,
        timeout_ms: 45000,
        enabled: true,
      },
    },
    {
      schema_version: 1,
      id: "skill",
      kind: "skills",
      name: "imported-test-skill",
      revision: "1",
      config: {
        name: "imported-test-skill",
        description: "Imported instructions",
        tags: ["example"],
        enabled: true,
      },
      archive_base64: sampleSkill(),
    },
    {
      schema_version: 1,
      id: "feishu",
      kind: "external-connections",
      name: "Imported Feishu",
      revision: "1",
      config: {
        name: "Imported Feishu",
        provider: "feishu",
        app_id: "cli_example",
        app_secret: "feishu-wire-secret",
        enabled: true,
      },
    },
  ];
  service.registerApplication({
    id: "integration-source",
    name: "Integration fixture",
  });
  service.registerIntegrationAdapter({
    id: "sample-adapter",
    application_id: "integration-source",
    name: "Sample",
    kinds: ["tool-apis", "mcp", "skills", "external-connections"],
    async list() {
      return records.map((r) => ({
        id: r.id,
        name: r.name,
        kind: r.kind,
        revision: r.revision,
        secret_fields: ["credentials"],
      }));
    },
    async export(kind, id) {
      return structuredClone(
        records.find((r) => r.id === id && r.kind === kind),
      );
    },
    async import() {
      throw new Error("Not a destination");
    },
    async receipt() {
      return { status: "not-found" };
    },
  });
  await service.capabilities();
  const adapters = (await api("/v1/integrations/dsh-adapters")).items;
  const adapter = adapters.find((a) => a.id === "sample-adapter");
  assert.ok(adapter);
  assert.ok(adapters.some((a) => a.id === "core-managed"));
  const selections = records.map(({ kind, id }) => ({ kind, id }));
  let plan = await api("/v1/integrations/transfers/preview", {
    direction: "from-dsh",
    bridge_id: adapter.bridge_id,
    adapter_id: adapter.id,
    items: selections,
  });
  for (const secret of [
    "fixture-wire-secret",
    "mcp-wire-secret",
    "feishu-wire-secret",
  ])
    assert.ok(!JSON.stringify(plan).includes(secret));
  const choices = plan.rows.map((row) => ({ key: row.key, action: "create" }));
  const applied = await api(
    "/v1/integrations/transfers/" + plan.id + "/apply",
    { choices },
  );
  assert.equal(applied.status, "completed", JSON.stringify(applied));
  assert.equal(calls, 0, "Import must not call external services");
  assert.deepEqual(
    await api("/v1/integrations/transfers/" + plan.id + "/apply", { choices }),
    applied,
  );
  for (const kind of ["tool-apis", "mcp", "skills", "external-connections"]) {
    const catalog = await api("/v1/integrations/" + kind);
    assert.equal(catalog.items.length, 1);
    assert.equal(catalog.items[0].enabled, false);
  }
  const coreItems = applied.rows.map((row) => ({
    kind: row.kind,
    id: row.result.id,
  }));
  const managed = adapters.find((a) => a.id === "core-managed");
  plan = await api("/v1/integrations/transfers/preview", {
    direction: "to-dsh",
    bridge_id: managed.bridge_id,
    adapter_id: managed.id,
    items: coreItems,
  });
  const returned = await api(
    "/v1/integrations/transfers/" + plan.id + "/apply",
    { choices: plan.rows.map((r) => ({ key: r.key, action: "create" })) },
  );
  assert.equal(returned.status, "completed", JSON.stringify(returned));
  const copies = await service.invoke({
    method: "integrations.copies",
    params: {},
  });
  assert.ok(copies.ok);
  assert.equal(copies.value.length, 4);
  assert.ok(copies.value.every((r) => !r.enabled));
  assert.equal(calls, 0);
  const encrypted = await readFile(
    root + "/host-profile/.runtime/fpl-core-integrations/catalog.enc",
  );
  for (const secret of [
    "fixture-wire-secret",
    "mcp-wire-secret",
    "feishu-wire-secret",
  ])
    assert.ok(!encrypted.includes(Buffer.from(secret)));
  const apiCopy = copies.value.find((r) => r.kind === "tool-apis");
  assert.ok(
    (
      await service.invoke({
        method: "integrations.enable",
        params: { id: apiCopy.id, enabled: true },
      })
    ).ok,
  );
  const tool = [...tools.values()].find((t) => t.name.startsWith("fpl_api_"));
  assert.ok(tool);
  const result = await tool.execute(
    { input: { message: "hello" } },
    { signal: new AbortController().signal },
  );
  assert.equal(calls, 1);
  assert.ok(!JSON.stringify(result).includes("fixture-wire-secret"));
  const skill = copies.value.find((r) => r.kind === "skills");
  assert.ok(
    (
      await service.invoke({
        method: "integrations.enable",
        params: { id: skill.id, enabled: true },
      })
    ).ok,
  );
  const published = skills.get("imported-test-skill");
  assert.ok(published);
  assert.equal(
    await readFile(
      published.resourceBase.path + "/references/reference.txt",
      "utf8",
    ),
    "Roundtrip attachment",
  );
});
