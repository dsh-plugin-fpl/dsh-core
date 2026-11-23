import { createTypertRemoteInfo } from "@deepseek-ai/dsh-typert-protocol/remote-info";
import { z } from "zod";
import { methods } from "./contracts.js";
const request = z
  .object({ method: z.enum(methods), params: z.record(z.string(), z.json()) })
  .strict();
const result = z.discriminatedUnion("ok", [
  z.object({ ok: z.literal(true), value: z.json() }),
  z.object({
    ok: z.literal(false),
    error: z.object({ code: z.string(), message: z.string() }),
  }),
]);
const codec = (typeSymbol: string, create: () => z.ZodType) => ({
  mode: "strict" as const,
  typeSymbol,
  create,
});
export const remoteInfo = createTypertRemoteInfo(
  {
    package: "@dsh-plugin-fpl/dsh-core",
    methods: {
      "dshCore/invoke": {
        service: "dshCore",
        invocation: { kind: "direct" },
        description:
          "Read Core bridge status or save this profile’s pairing credential.",
        parameters: [
          {
            name: "request",
            source: "json",
            codec: codec("DshCoreRequest", () => request),
            strictCodec: codec("DshCoreRequest", () => request),
          },
        ],
        result: codec("DshCoreResult", () => result),
        cancellation: { parameter: "signal" },
      },
    },
  },
  z,
);
