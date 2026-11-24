import test from "node:test";
import assert from "node:assert/strict";
import { Context } from "@deepseek-ai/cordis";
import { registerNativeTools, hostSchema } from "../lib/native-tools.js";
import { assertSupportedJsonSchema } from "@deepseek-ai/dsh-tools";

test("Host schema projection expands references while complete execution validation stays strict", () => {
  const full = { type: "object", properties: { document: { $ref: "#/$defs/Document" } }, required: ["document"],
    $defs: { Document: { type: "object", properties: { title: { type: "string", minLength: 2 },
      parent: { anyOf: [{ $ref: "#/$defs/Document" }, { type: "null" }] } }, additionalProperties: false } } };
  const projected = hostSchema(full);
  assertSupportedJsonSchema(projected);
  assert.equal(projected.properties.document.properties.title.type, "string");
  assert.match(projected.properties.document.properties.title.description, /minLength/);
});

test("workshop native definitions validate args, preserve cancellation and unregister", async () => {
  const ctx = new Context(), tools = new Map(), calls = [];
  ctx.provide("tools", { register(tool) { tools.set(tool.name, tool); return () => tools.delete(tool.name); } });
  const spec = { name: "fpl_fixture_edit", title: "编辑隔离项目", description: "Edit the current revision",
    method: "POST", path: "/v1/fixture/{project_id}", inputSchema: { type: "object", properties: {
      path: { type: "object", properties: { project_id: { type: "string", minLength: 1 } }, required: ["project_id"], additionalProperties: false },
      body: { type: "object", properties: { expected_revision: { type: "integer", minimum: 0 } }, required: ["expected_revision"], additionalProperties: false } }, required: ["path", "body"], additionalProperties: false } };
  const remove = registerNativeTools(ctx, [spec], async (operation, args, execution) => { calls.push({ operation, args, execution }); return { id: "fixture-task", status: "queued" }; });
  await new Promise(resolve => setTimeout(resolve, 0));
  const tool = tools.get(spec.name), controller = new AbortController();
  assert.ok(tool);
  await assert.rejects(() => tool.execute({ path: { project_id: "p" } }, { signal: controller.signal }), error => error.code === "FPL_INVALID_ARGS");
  assert.equal(calls.length, 0);
  const args = { path: { project_id: "p" }, body: { expected_revision: 2 } };
  const value = await tool.execute(args, { signal: controller.signal, agent: { id: "fixture-agent" } });
  assert.equal(value.status, "queued"); assert.equal(calls[0].execution.signal, controller.signal);
  assert.equal(tool.output.presentationMeta(args, value).task_id, "fixture-task");
  assert.equal(tool.presentCall().card, "generic");
  controller.abort();
  await assert.rejects(() => tool.execute(args, { signal: controller.signal }), { name: "AbortError" });
  assert.equal(calls.length, 1);
  await remove(); assert.equal(tools.size, 0);
});
