/** Public plugin API, owned by dsh-core. */
import type { IntegrationAdapter } from "./integrations/types.js";
export type * from "./integrations/types.js";
export const capabilities = [
  "chat",
  "vision",
  "tools",
  "image_generation",
  "video_generation",
  "video_enhancement",
  "speech_synthesis",
  "speech_recognition",
  "voice",
  "audio_generation",
] as const;
export type Capability = (typeof capabilities)[number];
export type Json =
  null | boolean | number | string | Json[] | { [key: string]: Json };
export type TaskStatus =
  | "queued"
  | "running"
  | "processing"
  | "recoverable"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";
export interface Execution {
  request_id: string;
  application_id: string;
  session_id?: string;
  kind?: "model" | "agent" | "local" | "integration";
  capability?: Capability;
  connection_id?: string;
  model?: string;
  prompt?: string;
  messages?: {
    role: "user" | "assistant";
    content: string;
    resource_ids?: string[];
  }[];
  parameters?: Record<string, Json>;
  resource_ids?: string[];
}
export interface ToolDefinition {
  name: string;
  description: string;
  inputSchema: Record<string, Json>;
  execute(
    arguments_: Record<string, Json>,
    context: { sessionId: string; signal: AbortSignal },
  ): Promise<Json>;
}
/** A plugin-owned local web entry displayed by the independent FPL dashboard. */
export interface ApplicationDashboard {
  description?: string;
  category?: "visual" | "knowledge" | "media" | "development";
  capabilities?: string[];
  package_name?: string;
  version?: string;
  url?: string;
  icon?:
    | "app"
    | "image"
    | "notebook"
    | "film"
    | "code"
    | "audio"
    | "presentation"
    | "table";
  icon_url?: string;
}
export interface Application {
  id: string;
  name: string;
  tools?: readonly ToolDefinition[];
  dashboard?: ApplicationDashboard;
}
export interface Resource {
  id: string;
  name: string;
  mime_type: string;
  byte_size: number;
  sha256: string;
  application_id: string;
}
export interface Task {
  id: string;
  request: Execution;
  status: TaskStatus;
  progress: number;
  text: string;
  result: Json;
  error: null | { code: string; message: string };
  created_at: number;
  updated_at: number;
  provider_task_id: string;
}
export interface Model {
  id: string;
  label: string;
  capabilities: Capability[];
  enabled: boolean;
}
export interface Connection {
  id: string;
  name: string;
  kind: string;
  base_url: string;
  credential_ref: string;
  models: Model[];
  enabled: boolean;
}
export interface Revision<T> {
  revision: number;
  value: T;
}
export interface State {
  connections: Revision<Connection[]>;
  defaults: Revision<
    Record<
      string,
      {
        kind?: "model" | "agent" | "local" | "integration";
        connection_id?: string;
        model: string;
      }
    >
  >;
  agents: Revision<
    Record<
      string,
      {
        id: string;
        executable?: string;
        enabled: boolean;
        model?: string;
        options?: Record<string, Json>;
      }
    >
  >;
  settings: Revision<Record<string, Json>>;
  tasks: Task[];
  resources: Resource[];
  local: { revision: number; items: LocalModel[] };
  provider_capabilities: Record<string, Capability[]>;
  harness: { id: string; name: string; models: Model[] }[];
}
export interface LocalModel {
  id: string;
  label: string;
  download_bytes: number;
  required_disk_bytes: number;
  installed: boolean;
  enabled: boolean;
  compatible: boolean;
  loaded: boolean;
  busy: boolean;
  capability: Capability;
}
export type CoreResult<T = Json> =
  | { ok: true; value: T }
  | { ok: false; error: { code: string; message: string } };
export const methods = [
  "state",
  "credential.set",
  "integrations.copies",
  "integrations.enable",
] as const;
export interface RpcRequest {
  method: (typeof methods)[number];
  params: Record<string, Json>;
}
export interface TaskEvent {
  seq: number;
  kind: string;
  value: Json;
  created_at: number;
}
export interface CoreApi {
  registerIntegrationAdapter(adapter: IntegrationAdapter): () => void;
  events(id: string, after?: number): Promise<TaskEvent[]>;
  registerApplication(application: Application): () => void;
  submit(request: Execution, signal?: AbortSignal): Promise<Task>;
  task(id: string): Promise<Task>;
  cancel(id: string): Promise<Task>;
  resource(id: string): Promise<Resource & { data_base64: string }>;
  resourceChunk(
    id: string,
    offset?: number,
  ): Promise<
    Resource & {
      data_base64: string;
      offset: number;
      next_offset: number;
      eof: boolean;
    }
  >;
  importResource(
    data: Uint8Array,
    metadata: { name: string; mime_type: string; application_id: string },
    signal?: AbortSignal,
  ): Promise<Resource>;
  putResource(data: {
    name: string;
    mime_type: string;
    data_base64: string;
    application_id: string;
  }): Promise<Resource>;
}
