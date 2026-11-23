/** A consumer depends only on dsh-core's published API. */
import type { Context } from "@deepseek-ai/cordis";
import type { Task } from "@dsh-plugin-fpl/dsh-core";
export const inject = ["dshCore"];
export function apply(ctx: Context) {
  ctx.effect(() =>
    ctx.dshCore.registerApplication({
      id: "example-notes",
      name: "Example notes",
      tools: [
        {
          name: "read_note",
          description: "Read the example note",
          inputSchema: {
            type: "object",
            properties: {},
            additionalProperties: false,
          },
          async execute() {
            return {
              text: "An application uses a shared model through dsh-core.",
            };
          },
        },
      ],
    }),
  );
}
export async function summarize(
  ctx: Context,
  requestId: string,
  signal: AbortSignal,
): Promise<Task> {
  const task = await ctx.dshCore.submit(
    {
      application_id: "example-notes",
      session_id: "example",
      request_id: requestId,
      capability: "tools",
      prompt: "Use read_note and summarize the note.",
    },
    signal,
  );
  for (;;) {
    signal.throwIfAborted();
    const current = await ctx.dshCore.task(task.id);
    if (
      ["completed", "failed", "cancelled", "interrupted"].includes(
        current.status,
      )
    )
      return current;
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(done, 250);
      function done() {
        signal.removeEventListener("abort", cancel);
        resolve();
      }
      function cancel() {
        clearTimeout(timer);
        reject(signal.reason);
      }
      signal.addEventListener("abort", cancel, { once: true });
    });
  }
}
