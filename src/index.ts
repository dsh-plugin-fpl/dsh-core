import { Context } from "@deepseek-ai/cordis";
import Schema from "@deepseek-ai/schemastery";
import { TypertRemoteService } from "@deepseek-ai/dsh-typert-protocol";
import { credentialRef } from "@deepseek-ai/dsh-credentials";
import type {} from "@deepseek-ai/dsh-typert-registry";
import type { ContentBlock } from "@deepseek-ai/dsh-llm";
import { defineTool } from "@deepseek-ai/dsh-tools";
import { CoreConnection, ConnectionError } from "./connection.js";
import { ManagedIntegrations } from "./integrations/managed.js";
import { nativeAdapter } from "./integrations/native.js";
import {
  portableSchema,
  integrationKinds,
  type IntegrationAdapter,
} from "./integrations/types.js";
import { dashboardSchema } from "./applications.js";
import { LocalService, type LocalServiceOptions } from "./lifecycle.js";
import { RuntimeManager, type ManagedServiceOptions, type ManagedService } from "./runtime-manager.js";
import { registerNativeTools, type NativeToolSpec, type WorkshopExecutionContext } from "./native-tools.js";
import { SetupAuthorization } from "./setup-authorization.js";
import { remoteInfo } from "./remote.js";
import type {
  Application,
  CapabilityCatalog,
  CoreApi,
  CoreResult,
  Execution,
  Resource,
  RpcRequest,
  Task,
  TaskEvent,
} from "./contracts.js";
export * from "./contracts.js";
export { remoteInfo } from "./remote.js";
export const name = "dsh-core";
export const inject = ["typert", "credentials"];
export interface Config {
  endpoint?: string;
  credential?: string;
  serviceDirectory?: string;
  manageService?: boolean;
  runtimeRoot?: string;
}
export const Config: Schema<Config> = Schema.object({
  runtimeRoot: Schema.string().description("高级：托管运行环境目录；不影响现有数据位置"),
  manageService: Schema.boolean()
    .default(true)
    .description("随插件开关启动和正常停止独立服务"),
  serviceDirectory: Schema.string().description(
    "独立 Core 服务安装目录；留空使用本机安装包登记",
  ),
  endpoint: Schema.string()
    .default("http://127.0.0.1:9310")
    .description("独立 Core 服务地址"),
  credential: Schema.string()
    .default("DSH_CORE_BRIDGE")
    .description("Core 配对凭据名称"),
});
declare module "@deepseek-ai/cordis" {
  interface Context {
    dshCore: DshCore;
  }
}
export class DshCore extends TypertRemoteService implements CoreApi {
  private connection?: CoreConnection;
  private localServices = new Set<ManagedService>();
  readonly runtime: RuntimeManager;
  private setupAuthorization: SetupAuthorization;
  private integrationAdapters = new Map<string, IntegrationAdapter>();
  private managedIntegrations?: ManagedIntegrations;
  private integrationSupport = false;
  private initialization: Promise<void>;
  private synchronized: Promise<void> = Promise.resolve();
  private applications = new Map<string, Application>([
    ["core", { id: "core", name: "DeepSeek Harness" }],
  ]);
  private endpoint: string;
  private credential: string;
  constructor(ctx: Context, config: Config = {}) {
    super(ctx, "dshCore");
    this.endpoint = config.endpoint || "http://127.0.0.1:9310";
    this.credential = config.credential || "DSH_CORE_BRIDGE";
    this.runtime = new RuntimeManager(config.runtimeRoot);
    this.setupAuthorization = new SetupAuthorization(this.runtime, async (token) => {
      await this.ctx.credentials.set(credentialRef(this.credential), token);
      this.initialization = this.connect();
      await this.initialization;
    });
    ctx.effect(() => () => this.runtime.dispose());
    const url = new URL(this.endpoint);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.username ||
      url.password ||
      !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)
    )
      throw new Error("Core 连接仅支持本机服务地址");
    this.initialization = this.connect();
    this.initialization.catch(() => {});
    ctx.effect(() => () => this.connection?.close());
    ctx.effect(() =>
      ctx.typert.register({
        package: remoteInfo.package,
        face: "host",
        schemas: [],
        model: { services: [], events: [], objects: [] },
        invocations: remoteInfo.descriptors,
      }),
    );
    if (ctx.get("profileContext")) {
      this.managedIntegrations = new ManagedIntegrations(ctx);
      const adapter = this.managedIntegrations.adapter();
      this.integrationAdapters.set(adapter.id, adapter);
    }
    const native = nativeAdapter(ctx);
    this.integrationAdapters.set(native.id, native);
    const thisService = this;
    const publisher = setInterval(() => {
      if (this.connection?.connected) {
        void this.publishModels().catch(() => {});
        void this.publishIntegrationAdapters().catch(() => {});
      }
    }, 10000);
    publisher.unref();
    ctx.effect(() => () => clearInterval(publisher));
    const scope = ctx.inject(["tools"], (toolContext: any) => {
      const definitions = [
        defineTool({
          name: "fpl_core_capabilities",
          description: "查看 Core 已接入的模型、能力和默认选择，不包含密钥。",
          parameters: {},
          output: {
            schema: { type: "json" },
            render: (_args: any, value: any) => [
              { type: "text", text: JSON.stringify(value) },
            ],
          },
          async execute() {
            return JSON.parse(JSON.stringify(await thisService.capabilities()));
          },
        }),
        defineTool({
          name: "fpl_core_submit",
          description:
            "提交 FPL Core 模型或本地能力任务，立即返回持久任务编号。",
          parameters: {
            request: {
              type: "json",
              required: true,
              description:
                "任务字段：request_id,kind,capability,connection_id,model,prompt,parameters,resource_ids；application_id 由连接层提供。",
            },
          },
          output: {
            schema: { type: "json" },
            render: (_args: any, value: any) => [
              { type: "text", text: JSON.stringify(value) },
            ],
          },
          async execute(args: any, exec: any) {
            return JSON.parse(
              JSON.stringify(
                await thisService.submit(
                  { ...args.request, application_id: "core" },
                  exec.signal,
                ),
              ),
            );
          },
        }),
        defineTool({
          name: "fpl_core_task",
          description: "查询本连接提交的 FPL Core 任务进度及结果。",
          parameters: { id: { type: "string", required: true } },
          output: {
            schema: { type: "json" },
            render: (_args: any, value: any) => [
              { type: "text", text: JSON.stringify(value) },
            ],
          },
          async execute(args: any) {
            return JSON.parse(JSON.stringify(await thisService.task(args.id)));
          },
        }),
        defineTool({
          name: "fpl_core_cancel",
          description: "停止本连接提交的 FPL Core 任务。",
          parameters: { id: { type: "string", required: true } },
          output: {
            schema: { type: "json" },
            render: (_args: any, value: any) => [
              { type: "text", text: JSON.stringify(value) },
            ],
          },
          async execute(args: any) {
            return JSON.parse(
              JSON.stringify(await thisService.cancel(args.id)),
            );
          },
        }),
      ];
      for (const tool of definitions)
        toolContext.effect(() => toolContext.tools.register(tool));
    });
    ctx.effect(() => () => scope.dispose());
  }
  manageLocalService(options: ManagedServiceOptions) {
    const service = this.runtime.register(options);
    if (options.id !== "core") this.localServices.add(service);
    const dispose = service.dispose.bind(service);
    service.dispose = () => dispose().then(() => { this.localServices.delete(service); });
    return service;
  }
  localServiceConfiguration(id: string) { return this.runtime.configuration(id); }
  registerNativeTools(ctx: Context, specs: readonly NativeToolSpec[], request: (operation: { method: string; path: string }, args: any, execution: WorkshopExecutionContext) => Promise<any>) {
    return registerNativeTools(ctx, specs, request);
  }
  async stopLocalServices() {
    const result = await Promise.allSettled(
      [...this.localServices].map((service) => service.stop()),
    );
    const failures = result.filter(
      (row): row is PromiseRejectedResult => row.status === "rejected",
    );
    if (failures.length)
      throw new AggregateError(
        failures.map((row) => row.reason),
        "工坊服务未完成停止；Core 暂时保留运行",
      );
  }
  private async connect() {
    this.connection?.close();
    const token =
      (await this.ctx.credentials.resolve(credentialRef(this.credential)))
        ?.value || "";
    this.connection = new CoreConnection(this.endpoint, token, (m, p, s) =>
      this.host(m, p, s),
    );
    this.connection.on("connected", () => {
      this.synchronized = this.synchronize();
      this.synchronized.catch(() => {});
    });
  }
  private async synchronize() {
    const hello = await this.connection!.request("hello", { protocol: 1 });
    if (hello.features?.includes("application-catalog"))
      await this.connection!.request("applications.sync", {
        applications: [...this.applications.values()].map((app) =>
          this.registration(app),
        ),
      });
    else
      for (const application of this.applications.values())
        await this.publish(application);
    this.integrationSupport =
      hello.features?.includes("integration-transfers") || false;
    await this.publishIntegrationAdapters();
    await this.publishModels();
  }
  private publishIntegrationAdapters() {
    if (!this.integrationSupport) return Promise.resolve();
    return this.connection!.request("integrations.publish", {
      adapters: [...this.integrationAdapters.values()]
        .filter((a) => this.applications.has(a.application_id))
        .map((a) => ({
          id: a.id,
          application_id: a.application_id,
          name: a.name,
          kinds: a.kinds,
          can_import: a.can_import !== false,
          can_export: a.can_export !== false,
        })),
    });
  }
  registerIntegrationAdapter(adapter: IntegrationAdapter) {
    if (
      !/^[a-z][a-z0-9-]{0,79}$/.test(adapter.id) ||
      this.integrationAdapters.has(adapter.id) ||
      !this.applications.has(adapter.application_id) ||
      !adapter.name ||
      adapter.name.length > 160 ||
      !adapter.kinds.length ||
      adapter.kinds.some((kind) => !integrationKinds.includes(kind))
    )
      throw new ConnectionError(
        "invalid_adapter",
        "请先登记应用并使用唯一集成适配器 ID",
      );
    this.integrationAdapters.set(adapter.id, adapter);
    this.synchronized = this.synchronized.then(async () => {
      if (this.connection?.connected) await this.publishIntegrationAdapters();
    });
    this.synchronized.catch(() => {});
    return () => {
      if (this.integrationAdapters.get(adapter.id) !== adapter) return;
      this.integrationAdapters.delete(adapter.id);
      if (this.connection?.connected)
        void this.publishIntegrationAdapters().catch(() => {});
    };
  }
  private async publishModels() {
    const providers = await this.harnessModels();
    await this.connection!.request("models.publish", {
      providers: providers.map((p) => ({
        ...p,
        id: p.id.slice("harness:".length),
      })),
    });
  }
  private registration(application: Application) {
    return {
      id: application.id,
      name: application.name,
      ...(application.dashboard ? { dashboard: application.dashboard } : {}),
      tools:
        application.tools?.map(({ name, description, inputSchema }) => ({
          name,
          description,
          inputSchema,
        })) || [],
    };
  }
  private publish(application: Application) {
    return this.connection!.request(
      "application.register",
      this.registration(application),
    );
  }
  private async call<T = any>(
    method: string,
    params: unknown = {},
    signal?: AbortSignal,
  ): Promise<T> {
    await this.initialization;
    await this.connection!.ready(signal);
    await this.synchronized;
    return this.connection!.request<T>(method, params, signal);
  }
  registerApplication(application: Application) {
    if (
      !/^[a-z][a-z0-9-]{0,79}$/.test(application.id) ||
      this.applications.has(application.id)
    )
      throw new ConnectionError(
        "application_conflict",
        "应用 ID 已登记或格式不正确",
      );
    const tools = application.tools || [];
    if (
      new Set(tools.map((t) => t.name)).size !== tools.length ||
      tools.some((t) => !/^[-a-zA-Z0-9_]{1,80}$/.test(t.name))
    )
      throw new ConnectionError("tool_conflict", "工具名重复或格式不正确");
    const saved = {
      ...application,
      ...(application.dashboard
        ? { dashboard: dashboardSchema.parse(application.dashboard) }
        : {}),
      tools: tools.map((t) => ({
        ...t,
        inputSchema: structuredClone(t.inputSchema),
      })),
    };
    this.applications.set(application.id, saved);
    this.synchronized = this.synchronized.then(async () => {
      await this.initialization;
      if (this.connection?.connected) await this.publish(saved);
    });
    this.synchronized.catch(() => {});
    return () => {
      if (this.applications.get(application.id) !== saved) return;
      this.applications.delete(application.id);
      for (const [id, adapter] of this.integrationAdapters)
        if (adapter.application_id === application.id)
          this.integrationAdapters.delete(id);
      if (this.connection?.connected)
        void this.publishIntegrationAdapters().catch(() => {});
      if (this.connection?.connected)
        void this.connection
          .request("application.unregister", { id: application.id })
          .catch(() => {});
    };
  }
  capabilities() {
    return this.call<CapabilityCatalog>("capabilities");
  }
  async submit(request: Execution, signal?: AbortSignal): Promise<Task> {
    if (!this.applications.has(request.application_id))
      throw new ConnectionError(
        "application_unregistered",
        "请先登记调用方应用",
      );
    return this.call("task.submit", request, signal);
  }
  events(id: string, after = 0): Promise<TaskEvent[]> {
    return this.call("task.events", { id, after });
  }
  task(id: string): Promise<Task> {
    return this.call("task.get", { id });
  }
  cancel(id: string): Promise<Task> {
    return this.call("task.cancel", { id });
  }
  resource(id: string): Promise<Resource & { data_base64: string }> {
    return this.call("resource.read", { id });
  }
  resourceChunk(
    id: string,
    offset = 0,
    signal?: AbortSignal,
  ): Promise<
    Resource & {
      data_base64: string;
      offset: number;
      next_offset: number;
      eof: boolean;
    }
  > {
    return this.call("resource.chunk", { id, offset }, signal);
  }
  putResource(data: {
    name: string;
    mime_type: string;
    data_base64: string;
    application_id: string;
  }): Promise<Resource> {
    if (!this.applications.has(data.application_id))
      return Promise.reject(
        new ConnectionError("application_unregistered", "请先登记调用方应用"),
      );
    return this.call("resource.put", data);
  }
  async importResource(
    data: Uint8Array,
    metadata: { name: string; mime_type: string; application_id: string },
    signal?: AbortSignal,
  ): Promise<Resource> {
    if (!this.applications.has(metadata.application_id))
      throw new ConnectionError(
        "application_unregistered",
        "请先登记调用方应用",
      );
    const upload = await this.call(
      "resource.upload.begin",
      { ...metadata, byte_size: data.byteLength },
      signal,
    );
    try {
      for (let offset = 0; offset < data.byteLength; offset += 1024 * 1024)
        await this.call(
          "resource.upload.append",
          {
            id: upload.id,
            offset,
            data_base64: Buffer.from(
              data.subarray(offset, offset + 1024 * 1024),
            ).toString("base64"),
          },
          signal,
        );
      return await this.call(
        "resource.upload.finish",
        { id: upload.id },
        signal,
      );
    } catch (e) {
      await this.call("resource.upload.abort", { id: upload.id }).catch(
        () => {},
      );
      throw e;
    }
  }
  async importResourceStream(source: AsyncIterable<Uint8Array>,
    metadata: { name: string; mime_type: string; application_id: string; byte_size: number; sha256?: string }, signal?: AbortSignal): Promise<Resource> {
    if (!this.applications.has(metadata.application_id)) throw new ConnectionError("application_unregistered", "请先登记调用方应用");
    if (!Number.isSafeInteger(metadata.byte_size) || metadata.byte_size < 0 || metadata.byte_size > 256 * 1024 * 1024)
      throw new ConnectionError("resource_size", "Core 资源超过大小限制");
    const upload = await this.call("resource.upload.begin", { name: metadata.name, mime_type: metadata.mime_type,
      application_id: metadata.application_id, byte_size: metadata.byte_size }, signal);
    let offset = 0;
    try {
      for await (const data of source) {
        signal?.throwIfAborted();
        if (offset + data.byteLength > metadata.byte_size) throw new ConnectionError("resource_size", "资源流超过声明长度");
        for (let index = 0; index < data.byteLength; index += 1024 * 1024) {
          const chunk = data.subarray(index, index + 1024 * 1024);
          await this.call("resource.upload.append", { id: upload.id, offset, data_base64: Buffer.from(chunk).toString("base64") }, signal);
          offset += chunk.byteLength;
        }
      }
      if (offset !== metadata.byte_size) throw new ConnectionError("resource_size", "资源流未完整传输");
      const result = await this.call<Resource>("resource.upload.finish", { id: upload.id }, signal);
      if (metadata.sha256 && result.sha256 !== metadata.sha256) throw new ConnectionError("resource_digest", "Core 资源摘要不匹配");
      return result;
    } catch (error) { await this.call("resource.upload.abort", { id: upload.id }).catch(() => {}); throw error; }
  }
  async invoke(request: RpcRequest): Promise<CoreResult<any>> {
    try {
      const invocation = this.ctx.invocation;
      if (invocation && invocation.peer.principal.kind !== "owner")
        throw new ConnectionError("forbidden", "请使用此 Harness 的所有者身份");
      if (request.method.startsWith("setup.")) {
        const id = String(request.params.id || "core");
        switch (request.method) {
          case "setup.status": return { ok: true, value: await this.runtime.status() };
          case "setup.install": await this.runtime.install(id); break;
          case "setup.cancel": this.runtime.cancel(id); break;
          case "setup.start": await this.runtime.start(id); break;
          case "setup.stop":
            if (id === "core") await this.stopLocalServices();
            await this.runtime.stop(id); break;
          case "setup.open": return { ok: true, value: await this.runtime.openDashboard() };
          case "setup.authorize.begin": return { ok: true, value: await this.setupAuthorization.begin(request.params.ids) };
          case "setup.authorize.finish": return { ok: true, value: await this.setupAuthorization.finish() };
          default: throw new ConnectionError("setup_method", "未知安装操作");
        }
        return { ok: true, value: await this.runtime.status() };
      }
      if (request.method === "integrations.copies")
        return {
          ok: true,
          value: this.managedIntegrations
            ? await this.managedIntegrations.list()
            : [],
        };
      if (request.method === "integrations.enable") {
        if (!this.managedIntegrations)
          throw new ConnectionError(
            "profile_unavailable",
            "当前 DSH 未提供持久 profile",
          );
        return {
          ok: true,
          value: await this.managedIntegrations.enable(
            String(request.params.id),
            request.params.enabled === true,
          ),
        };
      }
      if (request.method === "credential.set") {
        const token = String(request.params.value || "").trim();
        if (!token || token.length > 1024)
          throw new ConnectionError(
            "credential_ref",
            "请填写看板生成的配对凭据",
          );
        await this.ctx.credentials.set(credentialRef(this.credential), token);
        this.initialization = this.connect();
        await this.initialization;
        return { ok: true, value: { configured: true } };
      }
      if (request.method === "state") {
        return {
          ok: true,
          value: {
            endpoint: this.endpoint,
            connected: this.connection?.connected || false,
          },
        };
      }
      throw new ConnectionError(
        "forbidden",
        "请在独立看板中管理模型、任务与资源",
      );
    } catch (e) {
      return {
        ok: false,
        error: {
            code: e instanceof Error && "code" in e ? String(e.code) : "core_error",
          message:
            e instanceof Error && "code" in e ? e.message : "Core 连接操作失败",
        },
      };
    }
  }
  private async harnessModels() {
    const llm = this.ctx.get("llm");
    if (!llm) return [];
    return Promise.all(
      llm.listProviders().map(async (provider) => ({
        id: "harness:" + provider.id,
        name: provider.name,
        models: (await llm.listModels(provider.id)).map((model) => ({
          id: model.id,
          label: model.name,
          enabled: true,
          capabilities: model.inputModalities?.includes("image")
            ? ["chat", "vision"]
            : ["chat"],
        })),
      })),
    );
  }
  private async host(
    method: string,
    params: any,
    signal: AbortSignal,
  ): Promise<unknown> {
    if (method.startsWith("integrations.")) {
      const adapter = this.integrationAdapters.get(params.adapter_id);
      if (!adapter || !this.applications.has(adapter.application_id))
        throw new ConnectionError("adapter_unavailable", "集成适配器未启用");
      if (method === "integrations.list") return adapter.list(signal);
      if (method === "integrations.export")
        return portableSchema.parse(
          await adapter.export(params.kind, params.id, signal),
        );
      if (method === "integrations.import") {
        if (adapter.can_import === false)
          throw new ConnectionError("adapter_readonly", "此适配器仅支持导出");
        const item = portableSchema.parse(params.item);
        if (!adapter.kinds.includes(item.kind))
          throw new ConnectionError(
            "unsupported_kind",
            "适配器不支持此集成类型",
          );
        return adapter.import(
          item,
          {
            request_id: params.request_id,
            target_id: params.target_id,
            expected_revision: params.expected_revision,
            create: params.create === true,
          },
          signal,
        );
      }
      if (method === "integrations.receipt")
        return adapter.receipt(params.request_id, signal);
      throw new ConnectionError("host_method", "未知集成操作");
    }
    if (method === "tool.call") {
      const tool = this.applications
        .get(params.application_id)
        ?.tools?.find((t) => t.name === params.name);
      if (!tool)
        throw new ConnectionError("tool_unavailable", "应用工具已停止");
      return tool.execute(params.arguments, {
        sessionId: params.session_id,
        signal,
      });
    }
    if (method === "harness.generate") {
      const llm = this.ctx.get("llm");
      if (!llm)
        throw new ConnectionError("harness_unavailable", "DSH 模型服务未加载");
      const request: Execution = params.request,
        provider = request.connection_id!.slice("harness:".length);
      const models = await llm.listModels(provider);
      if (!models.some((m) => m.id === request.model))
        throw new ConnectionError(
          "model_unavailable",
          "DSH 模型已更新，请重新选择",
        );
      const messages: any[] = [];
      for (const item of request.messages?.length
        ? request.messages
        : [
            {
              role: "user",
              content: request.prompt || "",
              resource_ids: request.resource_ids || [],
            },
          ]) {
        const content: ContentBlock[] = [{ type: "text", text: item.content }];
        for (const id of item.resource_ids || []) {
          const resource = await this.resource(id);
          const attachments = this.ctx.get("attachments") as any;
          if (!attachments) throw new Error("DSH attachments unavailable");
          const attachment = await attachments.saveImage({
            data: Buffer.from(resource.data_base64, "base64"),
            mediaType: resource.mime_type,
            name: resource.name,
          });
          content.push({ type: "image", attachment });
        }
        messages.push({ role: item.role, content });
      }
      let finished = false,
        usage: unknown = null;
      for await (const chunk of llm.stream({
        provider,
        model: request.model!,
        messages,
        system: String(request.parameters?.system || ""),
        temperature: Number(request.parameters?.temperature ?? 0.7),
        maxTokens: Number(request.parameters?.max_tokens ?? 4096),
        signal,
      })) {
        if (chunk.type === "text-delta")
          await this.call(
            "host.event",
            {
              id: params.task_id,
              kind: "assistant_delta",
              value: { text: chunk.text },
            },
            signal,
          );
        else if (chunk.type === "usage") usage = chunk.usage;
        else if (chunk.type === "finish") {
          finished = true;
          if (chunk.reason.kind === "error")
            throw new Error("DSH model failed");
        }
      }
      if (!finished) throw new Error("Incomplete DSH model stream");
      return { usage };
    }
    throw new ConnectionError("host_method", "宿主回调未登记");
  }
}
export async function* apply(ctx: Context, config: Config = {}) {
  let service: DshCore | undefined;
  let lifecycle: ManagedService | undefined;
  yield async () => {
    await service?.stopLocalServices();
    await lifecycle?.stop();
  };
  service = new DshCore(ctx, config);
  lifecycle = service.manageLocalService({
    id: "core",
    endpoint: config.endpoint || "http://127.0.0.1:9310",
    installation: new URL("./service-installation.json", import.meta.url),
    serviceDirectory: config.serviceDirectory,
    manageService: config.manageService,
    manifest: new URL("./runtime/service.json", import.meta.url),
  });
  // Keep the setup surface mounted even when installation is missing or repair is needed.
  await lifecycle.start().catch(error => service!.runtime.failed("core", error));
}
