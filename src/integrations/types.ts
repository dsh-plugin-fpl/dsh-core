import { z } from "zod";
export const integrationKinds = [
  "tool-apis",
  "mcp",
  "skills",
  "external-connections",
] as const;
export type IntegrationKind = (typeof integrationKinds)[number];
export interface IntegrationSummary {
  id: string;
  kind: IntegrationKind;
  name: string;
  revision: string;
  enabled?: boolean;
  secret_fields?: string[];
}
export interface PortableIntegration {
  schema_version: 1;
  id: string;
  kind: IntegrationKind;
  name: string;
  revision: string;
  config: Record<string, any>;
  archive_base64?: string;
  warnings?: string[];
}
export interface ImportOptions {
  target_id: string;
  expected_revision: string;
  create: boolean;
  request_id: string;
}
export interface IntegrationImportResult {
  id: string;
  kind: IntegrationKind;
  name: string;
  revision: string;
}
export interface IntegrationAdapter {
  id: string;
  application_id: string;
  name: string;
  kinds: readonly IntegrationKind[];
  can_import?: boolean;
  can_export?: boolean;
  list(signal: AbortSignal): Promise<IntegrationSummary[]>;
  export(
    kind: IntegrationKind,
    id: string,
    signal: AbortSignal,
  ): Promise<PortableIntegration>;
  import(
    item: PortableIntegration,
    options: ImportOptions,
    signal: AbortSignal,
  ): Promise<IntegrationImportResult>;
  receipt(
    requestId: string,
    signal: AbortSignal,
  ): Promise<{ status: string; result?: IntegrationImportResult }>;
}
export const portableSchema = z
  .object({
    schema_version: z.literal(1),
    id: z.string().min(1).max(240),
    kind: z.enum(integrationKinds),
    name: z.string().min(1).max(120),
    revision: z.string().max(128),
    config: z.record(z.string(), z.json()),
    archive_base64: z
      .string()
      .max(28 * 1024 * 1024)
      .optional(),
    warnings: z.array(z.string().max(500)).max(20).optional(),
  })
  .strict();
export function credentialFields(config: Record<string, any>) {
  return Object.entries(config).flatMap(([key, value]) =>
    ["headers", "environment"].includes(key) &&
    value &&
    typeof value === "object"
      ? Object.keys(value).map((v) => key + "." + v)
      : /secret|token|api_key|password/i.test(key) && value
        ? [key]
        : [],
  );
}
