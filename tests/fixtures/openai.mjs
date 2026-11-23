/** Deterministic OpenAI-compatible endpoint for real Harness UI acceptance. */
import { createServer } from "node:http";
const server = createServer(async (request, response) => {
  if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
    response.writeHead(404);
    response.end();
    return;
  }
  let raw = "";
  for await (const chunk of request) {
    raw += chunk;
    if (raw.length > 1024 * 1024) {
      response.writeHead(413);
      response.end();
      return;
    }
  }
  const body = JSON.parse(raw);
  response.writeHead(200, { "Content-Type": "text/event-stream" });
  response.write(
    "data: " +
      JSON.stringify({
        choices: [
          { delta: { content: "DSH Core 联调成功" }, finish_reason: null },
        ],
      }) +
      "\n\n",
  );
  response.write(
    "data: " +
      JSON.stringify({
        choices: [{ delta: {}, finish_reason: "stop" }],
        usage: { prompt_tokens: 3, completion_tokens: 5, total_tokens: 8 },
      }) +
      "\n\n",
  );
  response.end("data: [DONE]\n\n");
});
server.listen(18765, "127.0.0.1", () =>
  console.log("Fixture listening on 127.0.0.1:18765"),
);
for (const signal of ["SIGINT", "SIGTERM"])
  process.on(signal, () => server.close());
