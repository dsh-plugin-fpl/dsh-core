import { ImportedIntegrations } from "./ImportedIntegrations.js";
import { useEffect, useState } from "react";
import type { Context } from "@deepseek-ai/cordis";
import type { RemoteResult } from "@deepseek-ai/dsh-typert-protocol";
import type { CoreResult, RpcRequest } from "../contracts.js";
import { remoteInfo } from "../remote.js";
type Invoke = (
  method: RpcRequest["method"],
  params?: Record<string, any>,
) => Promise<any>;
function Panel({ api }: { api: Invoke }) {
  const [state, setState] = useState<{
      endpoint: string;
      connected: boolean;
    } | null>(null),
    [token, setToken] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    const refresh = () =>
      api("state")
        .then((v) => {
          if (active) setState(v);
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    void refresh();
    const timer = setInterval(refresh, 3000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [api]);
  return (
    <section style={{ maxWidth: 680, padding: 24 }}>
      <h2>FPL Core 连接</h2>
      <p>模型、智能体、任务与资源由独立服务管理。</p>
      <p>
        {state?.connected ? "已连接" : "未连接"} ·{" "}
        {state?.endpoint || "正在读取服务地址…"}
      </p>
      <a
        href={state?.endpoint || "http://127.0.0.1:9310"}
        target="_blank"
        rel="noreferrer"
      >
        打开独立看板
      </a>
      <p>首次打开看板请在 Core 安装目录运行 pnpm dashboard。</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          setError("");
          void api("credential.set", { value: token })
            .then(() => {
              setToken("");
              return api("state");
            })
            .then(setState)
            .catch((e) => setError(e.message))
            .finally(() => setBusy(false));
        }}
      >
        <label>
          配对凭据
          <input
            type="password"
            autoComplete="new-password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="在独立看板的外部连接页创建"
          />
        </label>
        <button disabled={busy || !token.trim()}>
          {busy ? "保存中…" : "保存并连接"}
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      <ImportedIntegrations api={api} />
    </section>
  );
}
export function apply(ctx: Context) {
  const services = ctx as any;
  ctx.effect(async () => {
    const unmount = await services.remote.$mount(remoteInfo);
    const fiber = ctx.inject(["remote.dshCore"], (scope: Context) => {
      const api: Invoke = async (method, params = {}) => {
        const transport = (await remoteInfo.methods["dshCore/invoke"].invoke(
          (scope as any).remote,
          { request: { method, params } },
        )) as RemoteResult<unknown>;
        if (!transport.ok) throw new Error(transport.error.message);
        const result = transport.value as CoreResult<any>;
        if (!result.ok) throw new Error(result.error.message);
        return result.value;
      };
      scope.effect(() =>
        services.slots.inject("settings.section", () =>
          services.slots.register(
            {
              name: "settings.section",
              id: "dsh-core",
              order: 45,
              label: () => "FPL Core 连接",
              inject: () => ({ api }),
            },
            Panel,
          ),
        ),
      );
    });
    return async () => {
      await fiber.dispose();
      await unmount();
    };
  });
}
