import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { transform } from "esbuild";
const source = await readFile(
  new URL(
    "../web/src/features/applications/registrationRows.ts",
    import.meta.url,
  ),
  "utf8",
);
const { code } = await transform(source, { loader: "ts", format: "esm" });
const { registrationRows } = await import(
  "data:text/javascript;base64," + Buffer.from(code).toString("base64")
);
test("independent ready applications survive a disconnected DSH registration and bookmarked IDs remain valid", () => {
  const row = (
    id,
    status,
    url = "http://127.0.0.1:9362/apps/presentation-workshop",
    package_name = "presentation",
  ) => ({ id, status, url, package_name });
  const cards = registrationRows([
    row("dsh", "offline"),
    row("service", "ready"),
    row("without-page", "no_entry", ""),
    row("another-package", "ready", undefined, "other"),
  ]);
  assert.equal(cards.length, 3);
  assert.equal(cards[0].id, "service");
  assert.equal(cards[0].status, "ready");
  assert.deepEqual(cards[0].registrationIds, ["dsh", "service"]);
  assert.equal(
    registrationRows([row("service", "ready"), row("dsh", "offline")])[0].id,
    "service",
  );
});
