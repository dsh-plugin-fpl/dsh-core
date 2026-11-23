import { readFile, writeFile } from "node:fs/promises";
import { readPluginInventory } from "@deepseek-ai/dsh-host-plugin-inventory";
import { LlmAdapter } from "@deepseek-ai/dsh-llm";
export const inject = ["dshCore", "llm", "skills", "tools"];
export function apply(ctx) {
  ctx.effect(() =>
    ctx.skills.register({
      name: "harness-packaged-skill",
      description: "Harness packaged skill",
      content: "Read the reference.",
      source: "runtime",
      resourceBase: {
        kind: "directory",
        path: process.env.CORE_FIXTURE_SKILL_ROOT,
      },
    }),
  );
  const controller = new AbortController();
  ctx.effect(() => () => controller.abort());
  class Fixture extends LlmAdapter {
    providerInfo(id) {
      return { id, name: "Core isolated fixture" };
    }
    async listModels() {
      return [
        {
          provider: "core-fixture",
          id: "fixture",
          name: "Fixture",
          inputModalities: ["text"],
        },
      ];
    }
    async *stream({ signal }) {
      signal?.throwIfAborted();
      yield { type: "text-delta", text: "FPL_CORE_TARBALL_OK" };
      yield { type: "finish", reason: { kind: "stop" } };
    }
  }
  ctx.effect(() => ctx.llm.registerAdapter(["core-fixture"], new Fixture()));
  const unregister = ctx.dshCore.registerApplication({
    id: "tarball-smoke",
    name: "Isolated tarball smoke",
    dashboard: {
      category: "development",
      package_name: "fpl-core-isolated-smoke",
      version: "0.0.1",
      url: process.env.CORE_FIXTURE_APP_URL,
      description: "Installed plugin registration",
      icon: "code",
    },
  });
  ctx.effect(() => unregister);
  ctx.effect(() =>
    ctx.dshCore.registerIntegrationAdapter({
      id: "harness-integration-source",
      application_id: "tarball-smoke",
      name: "Harness integration fixture",
      kinds: ["tool-apis", "external-connections"],
      can_import: false,
      async list() {
        return [
          { id: "api", kind: "tool-apis", name: "Harness tool", revision: "1" },
          {
            id: "feishu",
            kind: "external-connections",
            name: "Harness Feishu",
            revision: "1",
          },
        ];
      },
      async export(kind, id) {
        return {
          schema_version: 1,
          id,
          kind,
          name: kind === "tool-apis" ? "Harness tool" : "Harness Feishu",
          revision: "1",
          config:
            kind === "tool-apis"
              ? {
                  name: "Harness tool",
                  method: "GET",
                  url: process.env.CORE_FIXTURE_API_URL,
                  auth_kind: "bearer",
                  secret: "harness-copy-secret",
                  parameters_schema: { type: "object" },
                }
              : {
                  name: "Harness Feishu",
                  provider: "feishu",
                  app_id: "cli_fixture",
                  app_secret: "harness-feishu-secret",
                },
        };
      },
      async import() {
        throw new Error("readonly");
      },
      async receipt() {
        return { status: "not-found" };
      },
    }),
  );
  void (async () => {
    const pair = JSON.parse(
      await readFile(process.env.CORE_FIXTURE_PAIR_FILE, "utf8"),
    );
    const result = await ctx.dshCore.invoke({
      method: "credential.set",
      params: { value: pair.token },
    });
    if (!result.ok) throw new Error(result.error.message);
    const task = await ctx.dshCore.submit(
      {
        request_id: "tarball-native",
        application_id: "tarball-smoke",
        kind: "model",
        capability: "chat",
        connection_id: "harness:core-fixture",
        model: "fixture",
        prompt: "Test isolated native callback",
      },
      controller.signal,
    );
    let current;
    for (let i = 0; i < 200; i++) {
      controller.signal.throwIfAborted();
      current = await ctx.dshCore.task(task.id);
      if (["completed", "failed", "interrupted"].includes(current.status))
        break;
      await new Promise((r) => setTimeout(r, 50));
    }
    if (
      current?.status !== "completed" ||
      current.text !== "FPL_CORE_TARBALL_OK"
    )
      throw new Error(
        "Native task did not complete: " + JSON.stringify(current?.error),
      );
    const resource = await ctx.dshCore.importResource(
      Buffer.from("tarball resource"),
      {
        application_id: "tarball-smoke",
        name: "smoke.txt",
        mime_type: "text/plain",
      },
    );
    const content = await ctx.dshCore.resource(resource.id);
    if (
      Buffer.from(content.data_base64, "base64").toString() !==
      "tarball resource"
    )
      throw new Error("Resource mismatch");
    void (async () => {
      let copies = [];
      for (let i = 0; i < 400; i++) {
        controller.signal.throwIfAborted();
        const response = await ctx.dshCore.invoke({
          method: "integrations.copies",
          params: {},
        });
        if (response.ok) copies = response.value;
        if (copies.length === 4) break;
        await new Promise((r) => setTimeout(r, 100));
      }
      if (copies.length !== 4) throw new Error("Imported copies not received");
      if (copies.some((row) => row.enabled))
        throw new Error("Import unexpectedly enabled a copy");
      for (const row of copies.filter(
        (row) => row.kind !== "external-connections",
      )) {
        const result = await ctx.dshCore.invoke({
          method: "integrations.enable",
          params: { id: row.id, enabled: true },
        });
        if (!result.ok) throw new Error(result.error.message);
      }
      const api = ctx.tools
        .schemas()
        .find((t) => t.name.startsWith("fpl_api_"));
      if (!api) throw new Error("Imported API not registered");
      const result = await ctx.tools
        .get(api.name)
        .execute({ input: {} }, { signal: controller.signal });
      if (JSON.stringify(result).includes("harness-copy-secret"))
        throw new Error("Credential exposed");
      const skill = await ctx.skills.get("harness-packaged-skill");
      if (!skill) throw new Error("Skill not registered");
      if (
        (await readFile(
          skill.resourceBase.path + "/references/data.txt",
          "utf8",
        )) !== "Harness attachment"
      )
        throw new Error("Skill attachment missing");
      const mcp = ctx.tools
        .schemas()
        .find((t) => t.name.includes("fpl_") && t.name.includes("echo"));
      if (!mcp) throw new Error("Imported MCP not registered");
      await writeFile(
        process.env.CORE_FIXTURE_INTEGRATION_REPORT,
        JSON.stringify({
          status: "completed",
          four_disabled_copies: true,
          tool_registered: true,
          mcp_registered: true,
          skill_attachment: true,
        }),
        { mode: 0o600 },
      );
    })().catch(async () => {
      await writeFile(
        process.env.CORE_FIXTURE_INTEGRATION_REPORT,
        JSON.stringify({
          status: "failed",
          message: "Imported integration activation failed",
        }),
        { mode: 0o600 },
      );
    });
    await writeFile(
      process.env.CORE_FIXTURE_REPORT,
      JSON.stringify({
        status: "completed",
        inventory_metadata: (await readPluginInventory(ctx)).entries.filter(
          (r) => r.moduleName.includes("mcp"),
        ),
        settings_metadata: ctx
          .get("settings")
          ?.describe({ redactSecrets: true })
          .map((r) => ({
            ns: r.ns,
            name: r.value?.serverName,
            transport: r.value?.transport,
          })),
        text: current.text,
        task_id: current.id,
        resource_id: resource.id,
      }),
      { mode: 0o600 },
    );
  })().catch(async (error) => {
    await writeFile(
      process.env.CORE_FIXTURE_REPORT,
      JSON.stringify({ status: "failed", message: error.message }),
      { mode: 0o600 },
    );
  });
}
