import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
export const inject = ["dshCore", "llm", "profileContext"];
export function apply(ctx) {
  const controller = new AbortController();
  const unregister = ctx.dshCore.registerApplication({
    id: "core-verification",
    name: "Core integration verification",
  });
  ctx.effect(() => () => {
    controller.abort();
    unregister();
  });
  const destination = join(
    ctx.profileContext.home,
    ".runtime",
    "dsh-core-verification",
  );
  async function report(value) {
    await mkdir(destination, { recursive: true, mode: 0o700 });
    await writeFile(
      join(destination, "desktop.json"),
      JSON.stringify(value, null, 2),
      { mode: 0o600 },
    );
  }
  void (async () => {
    await report({ status: "starting" });
    let selected;
    for (const provider of ctx.llm.listProviders()) {
      const models = await ctx.llm.listModels(provider.id);
      const model = models.find((model) =>
        /flash/i.test(model.name + " " + model.id),
      );
      if (model) {
        selected = { provider: provider.id, model: model.id };
        break;
      }
    }
    if (!selected) throw new Error("No Flash model available");
    const task = await ctx.dshCore.submit(
      {
        request_id: randomUUID(),
        application_id: "core-verification",
        session_id: "desktop-smoke",
        kind: "model",
        capability: "chat",
        connection_id: "harness:" + selected.provider,
        model: selected.model,
        prompt: "Reply with DSH_CORE_HARNESS_OK only.",
        parameters: { max_tokens: 512 },
      },
      controller.signal,
    );
    await report({ status: "running", task_id: task.id, ...selected });
    for (let i = 0; i < 360; i++) {
      controller.signal.throwIfAborted();
      const current = await ctx.dshCore.task(task.id);
      if (
        ["completed", "failed", "cancelled", "interrupted"].includes(
          current.status,
        )
      ) {
        await report({
          status: current.status,
          text: current.text,
          error: current.error,
          task_id: task.id,
          ...selected,
        });
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    await ctx.dshCore.cancel(task.id);
    throw new Error("Smoke task timed out");
  })().catch((error) =>
    report({ status: "error", message: String(error.message) }),
  );
}
