import test from "node:test";
import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { resolve } from "node:path";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { Context } from "@deepseek-ai/cordis";
import { DshCore } from "../lib/index.js";
const base = resolve(".local/node-tests");
await mkdir(base, { recursive: true });
const python = resolve(".runtime/worker-venv/bin/python");
async function fixture(t) {
  const root = await mkdtemp(base + "/independent-");
  const reservation = createServer();
  await new Promise((r) => reservation.listen(0, "127.0.0.1", r));
  const port = reservation.address().port;
  await new Promise((r) => reservation.close(r));
  const endpoint = "http://127.0.0.1:" + port;
  let process,
    errors = "";
  const launch = () => {
    process = spawn(
      python,
      ["tests/fixtures/server.py", "--root", root, "--port", String(port)],
      { stdio: ["ignore", "ignore", "pipe"] },
    );
    process.stderr.on("data", (x) => (errors += x.toString()));
  };
  const stop = async () => {
    process.kill("SIGTERM");
    await new Promise((r) => {
      if (process.exitCode !== null) return r();
      process.once("exit", r);
    });
  };
  const waitReady = async () => {
    for (let i = 0; i < 100; i++) {
      try {
        if ((await fetch(endpoint + "/ready")).ok) return;
      } catch {}
      if (process.exitCode !== null) assert.fail(errors);
      await new Promise((r) => setTimeout(r, 50));
    }
    assert.fail(errors || "Core not ready");
  };
  launch();
  t.after(async () => {
    await stop();
    await rm(root, { recursive: true, force: true });
  });
  await waitReady();
  const pair = JSON.parse(await readFile(root + "/fixture-pair.json", "utf8"));
  const ctx = new Context();
  ctx.provide("credentials", {
    resolve: async () => ({ value: pair.token }),
    set: async () => {},
  });
  ctx.provide("typert", { register: () => () => {} });
  let service;
  const fiber = ctx.plugin({
    inject: ["credentials", "typert"],
    apply(scope) {
      service = new DshCore(scope, { endpoint });
    },
  });
  await fiber;
  t.after(() => fiber.dispose());
  return {
    service,
    ctx,
    root,
    endpoint,
    fiber,
    restart: async () => {
      await stop();
      launch();
      await waitReady();
    },
  };
}

test("standalone Core handshake, application scope, chunk upload and disposal", async (t) => {
  const { service, endpoint, fiber } = await fixture(t);
  await assert.rejects(
    service.submit({ request_id: "one", application_id: "missing" }),
    /登记/,
  );
  const remove = service.registerApplication({ id: "sample", name: "Sample" });
  assert.throws(() =>
    service.registerApplication({ id: "sample", name: "Duplicate" }),
  );
  const data = Buffer.alloc(2 * 1024 * 1024 + 13, 73);
  const artifact = await service.importResource(data, {
    name: "clip.bin",
    mime_type: "application/octet-stream",
    application_id: "sample",
  });
  assert.match(artifact.application_id, /^b-/);
  const chunks = [];
  for (let offset = 0; ;) {
    const part = await service.resourceChunk(artifact.id, offset);
    chunks.push(Buffer.from(part.data_base64, "base64"));
    offset = part.next_offset;
    if (part.eof) break;
  }
  assert.deepEqual(Buffer.concat(chunks), data);
  remove();
  await assert.rejects(
    service.putResource({
      application_id: "sample",
      name: "x",
      mime_type: "text/plain",
      data_base64: "",
    }),
    /登记/,
  );
  await fiber.dispose();
  assert.ok(
    (await fetch(endpoint + "/ready")).ok,
    "DSH disposal must leave the independent Core alive",
  );
});

test("DSH native model streaming uses the durable independent task API", async (t) => {
  const { service, ctx } = await fixture(t);
  ctx.provide("llm", {
    listProviders: () => [{ id: "test", name: "Test" }],
    listModels: async () => [{ id: "model", name: "Model" }],
    async *stream() {
      yield { type: "text-delta", text: "Independent host reply" };
      yield { type: "finish", reason: { kind: "stop" } };
    },
  });
  service.registerApplication({ id: "example", name: "Example" });
  const task = await service.submit({
    request_id: "native",
    application_id: "example",
    kind: "model",
    connection_id: "harness:test",
    model: "model",
    prompt: "Hello",
  });
  for (let i = 0; i < 150; i++) {
    const current = await service.task(task.id);
    if (current.status === "completed") {
      assert.equal(current.text, "Independent host reply");
      return;
    }
    if (["failed", "interrupted"].includes(current.status))
      assert.fail(JSON.stringify(current.error));
    await new Promise((r) => setTimeout(r, 20));
  }
  assert.fail("Task did not settle");
});

test("only the Harness owner can save pairing credentials", async () => {
  const receiver = {
    ctx: { invocation: { peer: { principal: { kind: "guest" } } } },
  };
  const result = await DshCore.prototype.invoke.call(receiver, {
    method: "credential.set",
    params: { value: "forbidden" },
  });
  assert.equal(result.ok, false);
  assert.equal(result.error.code, "forbidden");
});

test("bridge settings no longer expose model configuration mutation", async (t) => {
  const { service } = await fixture(t);
  const result = await service.invoke({ method: "config.save", params: {} });
  assert.equal(result.ok, false);
  assert.equal(result.error.code, "forbidden");
});

test("closing DSH interrupts only the callback task and preserves partial output", async (t) => {
  const { service, ctx, root, endpoint, fiber } = await fixture(t);
  ctx.provide("llm", {
    listProviders: () => [{ id: "slow", name: "Slow" }],
    listModels: async () => [{ id: "model", name: "Model" }],
    async *stream({ signal }) {
      yield { type: "text-delta", text: "Partial output" };
      await new Promise((_, reject) => {
        signal.addEventListener("abort", () => reject(new Error("cancelled")), {
          once: true,
        });
      });
      yield { type: "finish", reason: { kind: "stop" } };
    },
  });
  service.registerApplication({ id: "slow-example", name: "Slow" });
  const task = await service.submit({
    request_id: "disconnect",
    application_id: "slow-example",
    kind: "model",
    connection_id: "harness:slow",
    model: "model",
    prompt: "Wait",
  });
  for (let i = 0; i < 100; i++) {
    if ((await service.task(task.id)).text === "Partial output") break;
    await new Promise((r) => setTimeout(r, 20));
  }
  assert.equal((await service.task(task.id)).text, "Partial output");
  await fiber.dispose();
  const ticket = await readFile(root + "/fixture-launch.txt", "utf8");
  const login = await fetch(endpoint + "/v1/session", {
    method: "POST",
    headers: { Origin: endpoint, "Content-Type": "application/json" },
    body: JSON.stringify({ ticket }),
  });
  assert.ok(login.ok);
  const cookies = login.headers
    .getSetCookie()
    .map((x) => x.split(";")[0])
    .join("; ");
  for (let i = 0; i < 100; i++) {
    const current = await (
      await fetch(endpoint + "/v1/tasks/" + task.id, {
        headers: { Cookie: cookies },
      })
    ).json();
    if (current.status === "interrupted") {
      assert.equal(current.text, "Partial output");
      assert.equal(current.error.code, "bridge_disconnected");
      return;
    }
    await new Promise((r) => setTimeout(r, 20));
  }
  assert.fail("Disconnected callback did not settle");
});

test("connector reconnects after Core restart without changing resource ownership", async (t) => {
  const { service, restart } = await fixture(t);
  service.registerApplication({ id: "persistent", name: "Persistent" });
  const before = await service.importResource(Buffer.from("persisted"), {
    name: "stable.txt",
    mime_type: "text/plain",
    application_id: "persistent",
  });
  await restart();
  let reconnected = false;
  for (let i = 0; i < 150; i++) {
    try {
      const current = await service.resource(before.id);
      assert.equal(current.application_id, before.application_id);
      reconnected = true;
      break;
    } catch {
      await new Promise((r) => setTimeout(r, 50));
    }
  }
  assert.ok(reconnected, "Connector did not reconnect to Core");
  const after = await service.importResource(Buffer.from("after restart"), {
    name: "next.txt",
    mime_type: "text/plain",
    application_id: "persistent",
  });
  assert.equal(after.application_id, before.application_id);
});

test("installed application metadata reaches dashboard, unregisters, and survives reconnect", async (t) => {
  const { service, root, endpoint, restart, fiber } = await fixture(t);
  const metadata = {
    id: "plugin-notes",
    name: "插件笔记",
    dashboard: {
      category: "knowledge",
      description: "Plugin supplied",
      url: "http://127.0.0.1:19320/",
      package_name: "dsh-notes",
      version: "1.0.0",
    },
  };
  assert.throws(() =>
    service.registerApplication({
      ...metadata,
      id: "invalid",
      dashboard: { url: "javascript:alert(1)" },
    }),
  );
  const remove = service.registerApplication(metadata);
  async function catalog() {
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
    return () =>
      fetch(endpoint + "/v1/applications", { headers: { Cookie: cookie } })
        .then((r) => r.json())
        .then((x) => x.items);
  }
  await service.capabilities();
  let entries = await catalog();
  assert.equal((await entries())[0].package_name, "dsh-notes");
  await restart();
  await service.capabilities();
  entries = await catalog();
  assert.equal((await entries())[0].status, "ready");
  remove();
  for (let i = 0; i < 100 && (await entries()).length; i++)
    await new Promise((r) => setTimeout(r, 20));
  assert.equal((await entries()).length, 0);
  service.registerApplication(metadata);
  await service.capabilities();
  await fiber.dispose();
  assert.equal((await entries())[0].status, "offline");
});
