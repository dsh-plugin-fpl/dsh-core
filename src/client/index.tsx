import { ImportedIntegrations } from "./ImportedIntegrations.js";
import { SetupWizard } from "./SetupWizard.js";
import { useEffect, useRef, useState } from "react";
import type { Context } from "@deepseek-ai/cordis";
import type { RemoteResult } from "@deepseek-ai/dsh-typert-protocol";
import type { CoreResult, RpcRequest } from "../contracts.js";
import { remoteInfo } from "../remote.js";
type Invoke = (
  method: RpcRequest["method"],
  params?: Record<string, any>,
) => Promise<any>;
function Panel({ api, close }: { api: Invoke; close?: () => void }) {
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    const query = matchMedia("(max-width: 600px)");
    const focus = () => { if (query.matches) panel.current?.querySelector<HTMLButtonElement>(".fpl-core-settings-close")?.focus(); };
    focus(); query.addEventListener("change", focus);
    return () => query.removeEventListener("change", focus);
  }, []);
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
    <section className="fpl-core-settings" ref={panel} onKeyDown={event => {
      if (event.key !== "Tab" || !matchMedia("(max-width: 600px)").matches) return;
      const controls = [...(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),a[href],summary') || [])].filter(element => element.getClientRects().length);
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last) {
        event.preventDefault(); event.stopPropagation(); (event.shiftKey ? last : first)?.focus();
      }
    }}>
      <style>{`.fpl-core-settings{box-sizing:border-box;max-width:680px;padding:24px;overflow-wrap:anywhere}.fpl-core-settings-close{display:none}@media(max-width:600px){.fpl-core-settings{position:fixed;inset:12px;z-index:10000;max-width:none;padding:16px;background:var(--dsw-alias-bg-layer-2,#fff);color:var(--dsw-alias-label-primary,#222);overflow:auto;border-radius:12px;box-shadow:0 12px 60px #0003}.fpl-core-settings-close{display:block;margin-left:auto;min-height:36px}.fpl-core-settings h2{font-size:22px}.fpl-core-settings input{max-width:100%}}`}</style>
      <button className="fpl-core-settings-close" onClick={close}>关闭安装设置</button>
      <h2>FPL 安装与连接</h2>
      <p>模型、智能体、任务与资源由独立服务管理。</p>
      <p>
        {state?.connected ? "已连接" : "未连接"} ·{" "}
        {state?.endpoint || "正在读取服务地址…"}
      </p>
      <SetupWizard api={api} />
      <details><summary>手动连接已有服务</summary>
      <a
        href={state?.endpoint || "http://127.0.0.1:9310"}
        target="_blank"
        rel="noreferrer"
      >
        打开独立看板
      </a>
      <p>已有独立服务可在看板的外部连接页创建配对凭据；新安装请使用上方安装向导。</p>
      <p>
        <a
          href={(state?.endpoint || "http://127.0.0.1:9310") + "/storage"}
          target="_blank"
          rel="noreferrer"
        >
          存储与迁移
        </a>{" "}
        · 在独立看板统一设置 Core 和各工坊的数据位置。
      </p>
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
      </details>
      {error && <p role="alert">{error}</p>}
      <ImportedIntegrations api={api} />
    </section>
  );
}
export const inject = ["remote", "slots"];
export async function apply(ctx: Context) {
  const services = ctx as any;
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
              label: () => "FPL 安装与连接",
              inject: () => ({ api }),
            },
            Panel,
          ),
        ),
      );
    });
    try { await fiber; }
    catch (error) { await fiber.dispose(); await unmount(); throw error; }
    return async () => {
      await fiber.dispose();
      await unmount();
    };
}
