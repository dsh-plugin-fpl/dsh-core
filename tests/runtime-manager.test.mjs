import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
import { zipSync, strToU8 } from "fflate";
import { RuntimeManager } from "../lib/runtime-manager.js";

async function fixture(t) {
  const base = await mkdtemp(join(tmpdir(), "fpl-managed-runtime-"));
  t.after(() => rm(base, { recursive: true, force: true }));
  const payload = join(base, "payload"); await mkdir(payload);
  const contents = { "package.json": strToU8('{"name":"@dsh-plugin-fpl/dsh-fixture"}') };
  const bytes = zipSync(contents), sha = data => createHash("sha256").update(data).digest("hex");
  const manifest = { protocol: 1, id: "dsh-fixture", package: "@dsh-plugin-fpl/dsh-fixture", name: "Fixture",
    module: "fpl_fixture", port: 19398, version: "0.0.1", platforms: ["darwin-arm64", "darwin-x64"],
    python: "3.13.15", payload: "service.zip", sha256: sha(bytes), byte_size: bytes.length,
    expanded_size: contents["package.json"].length, files: { "package.json": sha(contents["package.json"]) } };
  await writeFile(join(payload, "service.zip"), bytes);
  await writeFile(join(payload, "service.json"), JSON.stringify(manifest));
  const manager = new RuntimeManager(join(base, ".runtime"));
  const handle = manager.register({ id: manifest.id, endpoint: "http://127.0.0.1:19398",
    manifest: pathToFileURL(join(payload, "service.json")), installation: pathToFileURL(join(payload, "absent.json")) });
  t.after(() => manager.dispose());
  return { manager, handle, base, payload, manifest };
}
test("setup exists before installation and missing environments remain repairable", async t => {
  const { manager, handle } = await fixture(t);
  await assert.rejects(() => handle.start(), error => error.code === "setup_required");
  const [status] = await manager.status();
  assert.equal(status.phase, "missing"); assert.equal(status.installed, false);
  await assert.rejects(() => manager.install("unknown"), error => error.code === "service_unregistered");
  assert.throws(() => new RuntimeManager("/tmp/.local/service"), /\.local/);
});
test("a damaged payload fails before Python preparation and preserves no active record", async t => {
  const { manager, payload, base } = await fixture(t);
  await writeFile(join(payload, "service.zip"), "changed");
  await manager.install("dsh-fixture"); await manager.wait("dsh-fixture");
  const [status] = await manager.status();
  assert.equal(status.phase, "failed"); assert.equal(status.error.code, "payload_size");
  await assert.rejects(() => readFile(join(base, ".runtime/records/dsh-fixture.json")), { code: "ENOENT" });
});
test("unregistered paths and conflicting installations are rejected", async t => {
  const { manager, payload, manifest } = await fixture(t);
  await writeFile(join(payload, "service.json"), JSON.stringify({ ...manifest, id: "dsh-other" }));
  await assert.rejects(() => manager.install("dsh-fixture"), error => error.code === "service_manifest");
  assert.throws(() => manager.register({ id: "../other" }), /标识/);
});
