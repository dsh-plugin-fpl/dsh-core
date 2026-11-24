import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProgram } from "../lib/runtime-bootstrap.js";

test("installer cancellation waits for process exit before returning", async t => {
  const root = await mkdtemp(join(tmpdir(), "fpl-installer-cancel-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const file = join(root, "writes"), controller = new AbortController();
  const pending = runProgram(process.execPath, ["--input-type=module", "-e",
    "import{appendFileSync}from'node:fs';setInterval(()=>appendFileSync(process.argv[1],'x'),10);", file], root, controller.signal);
  for (let i = 0; i < 100; i++) {
    if (await readFile(file).then(value => value.length > 0, () => false)) break;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.ok((await readFile(file)).length);
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
  const count = (await readFile(file)).length;
  await new Promise(resolve => setTimeout(resolve, 80));
  assert.equal((await readFile(file)).length, count, "cancelled installer continued writing");
});

test("installer reports missing executables and bounded output", async () => {
  await assert.rejects(() => runProgram("/missing-fpl-installer", [], tmpdir()), { code: "ENOENT" });
  await assert.rejects(() => runProgram(process.execPath, ["-e", "process.stdout.write('x'.repeat(600000));setInterval(()=>{},1000)"], tmpdir()),
    error => error.code === "program_output");
});
