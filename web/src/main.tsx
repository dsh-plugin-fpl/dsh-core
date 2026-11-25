import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  CoreOwnerAccess,
  type CoreProfile,
} from "@platform/auth/AccountAccess";
import { requestApi, fetchApiJson, ApiError } from "@platform/api/client";
import { App } from "./App";
import { LoadingState } from "@shared/ui/LoadingState";
import "@shared/ui/theme.css";
import "@app/styles.css";
import "./core.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});
function Boot() {
  const [identity, setIdentity] = useState<{
    account: CoreProfile;
    permissions: string[];
  } | null>(null);
  const [ready, setReady] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    const fail = () => {
      setReady(false);
      setError("本机会话已失效，请刷新页面重新连接本机。");
    };
    window.addEventListener("core:auth-required", fail);
    const start = async () => {
      const ticket = new URLSearchParams(location.hash.slice(1)).get("ticket");
      if (ticket) {
        history.replaceState(null, "", location.pathname + location.search);
        await requestApi("/v1/session", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ticket }),
        });
      }
      try {
        await fetchApiJson("/v1/identity/me");
      } catch (cause) {
        if (!(cause instanceof ApiError) || cause.status !== 401) throw cause;
        await requestApi("/v1/session/local", { method: "POST" });
      }
      setIdentity(await fetchApiJson("/v1/identity/me"));
      setReady(true);
    };
    void start().catch((e) => setError(e.message));
    return () => window.removeEventListener("core:auth-required", fail);
  }, []);
  return ready && identity ? (
    <QueryClientProvider client={queryClient}>
      <CoreOwnerAccess
        profile={identity.account}
        permissions={identity.permissions}
      >
        <App />
      </CoreOwnerAccess>
    </QueryClientProvider>
  ) : (
    <main className="core-unlock">
      <h1>未来道具研究所</h1>
      {error ? (
        <p>{error}</p>
      ) : (
        <LoadingState label="正在连接本机服务…" variant="page" />
      )}
      {error && (
        <p>
          <button className="btn primary" onClick={() => location.reload()}>
            重新连接本机
          </button>
        </p>
      )}
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<Boot />);
