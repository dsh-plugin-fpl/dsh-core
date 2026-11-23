/** Validate owned snapshots before persisting or exposing them to the DSH runtime. */
import { z } from "zod";
import Ajv2020 from "ajv/dist/2020.js";
import { portableSchema, type PortableIntegration } from "./types.js";
const text = z
  .string()
  .max(8192)
  .refine((value) => !value.includes("\0"));
const url = z
  .url()
  .max(1024)
  .refine((value) => {
    const u = new URL(value);
    return (
      ["http:", "https:"].includes(u.protocol) &&
      !u.username &&
      !u.password &&
      !u.hash
    );
  });
const map = z.record(z.string().max(120), text);
const base = z.object({
  name: z.string().min(1).max(120),
  description: z.string().max(4000).default(""),
  tags: z.array(z.string()).max(12).default([]),
  enabled: z.boolean().default(false),
  sensitive_result: z.boolean().default(false),
});
const tool = base.extend({
  method: z.enum(["GET", "POST", "PUT", "PATCH", "DELETE"]).default("GET"),
  url,
  auth_kind: z.enum(["none", "bearer", "api_key"]).default("none"),
  auth_header_name: z.string().max(120).default(""),
  secret: text.default(""),
  parameters_schema: z.record(z.string(), z.json()).default({ type: "object" }),
  query_template: z.record(z.string(), z.json()).default({}),
  body_template: z.json().default(null),
});
const mcp = base.extend({
  transport: z.enum(["stdio", "streamable_http"]),
  url: z.string().default(""),
  command: text.default(""),
  arguments: z.array(z.string().max(4096)).max(64).default([]),
  environment: map.default({}),
  headers: map.default({}),
  bearer_token: text.default(""),
  cwd: z.string().max(4096).default(""),
  timeout_ms: z.number().int().min(1000).max(120000).default(30000),
});
const external = base.extend({
  provider: z.literal("feishu"),
  app_id: z.string().min(1).max(120),
  app_secret: text.min(1),
});
export function validateConfiguration(
  value: PortableIntegration,
): PortableIntegration {
  const item = portableSchema.parse(value) as PortableIntegration;
  try {
    const config =
      item.kind === "tool-apis"
        ? tool.parse(item.config)
        : item.kind === "mcp"
          ? mcp.parse(item.config)
          : item.kind === "external-connections"
            ? external.parse(item.config)
            : base.parse(item.config);
    if (item.kind === "tool-apis") {
      const t = config as z.infer<typeof tool>;
      if (t.parameters_schema.type !== "object") throw new Error();
      new (Ajv2020 as any)({ strict: false }).compile(t.parameters_schema);
      if (
        t.auth_kind === "api_key" &&
        (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(t.auth_header_name) ||
          [
            "host",
            "connection",
            "content-length",
            "transfer-encoding",
          ].includes(t.auth_header_name.toLowerCase()))
      )
        throw new Error();
    }
    if (item.kind === "mcp") {
      const c = config as z.infer<typeof mcp>;
      if (c.transport === "stdio" ? !c.command : !url.safeParse(c.url).success)
        throw new Error();
      for (const [k, v] of Object.entries(c.headers))
        if (
          !/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(k) ||
          [
            "host",
            "connection",
            "content-length",
            "transfer-encoding",
          ].includes(k.toLowerCase()) ||
          /[\r\n]/.test(v)
        )
          throw new Error();
      for (const k of Object.keys(c.environment))
        if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(k)) throw new Error();
    }
    return { ...item, config: { ...config, name: item.name, enabled: false } };
  } catch {
    throw new Error("集成配置不符合目标契约，请检查后重新预览");
  }
}
