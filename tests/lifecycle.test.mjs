import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { Context } from "@deepseek-ai/cordis";
import { LocalService } from "../lib/lifecycle.js";

async function fixture(t) {
  // Production rejects .local service installations. This library-managed
  // system fixture is short-lived and never reads a machine storage locator.
  const root = await mkdtemp(join(tmpdir(), "dsh-lifecycle-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(join(root, "scripts"));
  await writeFile(
    join(root, "package.json"),
    JSON.stringify({ name: "@dsh-plugin-fpl/dsh-fixture", type: "module" }),
  );
  await writeFile(
    join(root, "scripts/application.mjs"),
    `
import {appendFile,access} from 'node:fs/promises';
const action=process.argv[2];
await appendFile('trace',action+'.begin\\n');
if(action==='start'){try{await access('fail');process.stderr.write('isolated refusal');process.exit(3)}catch{}}
await new Promise(r=>setTimeout(r,100));
await appendFile('trace',action+'.end\\n');
`,
  );
  const options = {
    id: "dsh-fixture",
    endpoint: "http://127.0.0.1:19398",
    serviceDirectory: root,
    installation: pathToFileURL(join(root, "unused.json")),
  };
  const trace = async () => {
    try {
      return (await readFile(join(root, "trace"), "utf8")).trim().split("\n");
    } catch {
      return [];
    }
  };
  return { root, options, trace };
}
test("rapid off/on serializes public start/stop and preserves the new instance", async (t) => {
  const { options, trace } = await fixture(t);
  const first = new LocalService(options);
  const started = first.start();
  for (let i = 0; i < 100 && !(await trace()).includes("start.begin"); i++)
    await new Promise((r) => setTimeout(r, 10));
  const stopped = first.stop(),
    second = new LocalService(options),
    restarted = second.start();
  await Promise.all([started, stopped, restarted]);
  assert.deepEqual(await trace(), [
    "start.begin",
    "start.end",
    "stop.begin",
    "stop.end",
    "start.begin",
    "start.end",
  ]);
  await second.stop();
  await second.stop();
  assert.deepEqual((await trace()).slice(-2), ["stop.begin", "stop.end"]);
});
test("failed start surfaces a log path and never stops a preexisting service", async (t) => {
  const { root, options, trace } = await fixture(t);
  await writeFile(join(root, "fail"), "");
  const service = new LocalService(options);
  await assert.rejects(service.start(), /启动失败.*日志/);
  await service.stop();
  assert.deepEqual(await trace(), ["start.begin"]);
  assert.match(
    await readFile(
      join(root, ".runtime/plugin-lifecycle/dsh-fixture.log"),
      "utf8",
    ),
    /isolated refusal/,
  );
});
test("missing or wrong installations fail before any launcher runs", async (t) => {
  const { root, options, trace } = await fixture(t);
  await writeFile(
    join(root, "package.json"),
    JSON.stringify({ name: "other-package" }),
  );
  await assert.rejects(new LocalService(options).start(), /其他应用/);
  assert.deepEqual(await trace(), []);
  await assert.rejects(
    new LocalService({
      ...options,
      serviceDirectory: join(root, ".local/service"),
    }).start(),
    /不能使用/,
  );
  const manual = new LocalService({ ...options, manageService: false });
  await manual.start();
  await manual.stop();
  assert.deepEqual(await trace(), []);
});
test("Cordis awaits managed teardown and rolls back failed startup", async (t) => {
  const { root, options, trace } = await fixture(t);
  const ctx = new Context();
  const plugin = {
    async *apply() {
      const service = new LocalService(options);
      yield () => service.stop();
      await service.start();
    },
  };
  const fiber = ctx.plugin(plugin);
  await fiber;
  await fiber.dispose();
  assert.deepEqual(await trace(), [
    "start.begin",
    "start.end",
    "stop.begin",
    "stop.end",
  ]);
  await writeFile(join(root, "fail"), "");
  const failed = ctx.plugin(plugin);
  await assert.rejects(Promise.resolve(failed), /启动失败/);
  await failed.dispose();
  assert.equal((await trace()).at(-1), "start.begin");
});
