import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchApiJson } from "@platform/api/client";
import "./creative-workshop.css";

export type WebsiteStatus = {
  site: string;
  account: { id: string; username: string; display_name: string } | null;
  permissions: string[];
  must_change_password: boolean;
  offline?: boolean;
};
export const websiteKey = ["fpl-website"];
export const useWebsite = () => {
  const cache = useQueryClient();
  useEffect(() => {
    const expired = () =>
      void cache.invalidateQueries({ queryKey: websiteKey });
    window.addEventListener("fpl:auth-required", expired);
    return () => window.removeEventListener("fpl:auth-required", expired);
  }, [cache]);
  return useQuery({
    queryKey: websiteKey,
    queryFn: () => fetchApiJson<WebsiteStatus>("/v1/fpl-connection"),
    retry: false,
    staleTime: 30000,
  });
};

export function WebsiteAccount() {
  const status = useWebsite();
  const cache = useQueryClient();
  const [site, setSite] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const current = status.data;
  const submit = async (action: string, body = {}) => {
    setBusy(true);
    setError("");
    try {
      await fetchApiJson("/v1/fpl-connection/" + action, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setPassword("");
      setNewPassword("");
      cache.removeQueries({ queryKey: ["creative-workshop"] });
      await cache.invalidateQueries({ queryKey: websiteKey });
      window.dispatchEvent(new Event("fpl:account-changed"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "连接失败");
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="page creative-page website-account">
      <h1>FPL 网站账号</h1>
      <p>
        本机功能已以本机所有者身份就绪。登录网站用于发现、发布和安装创意工坊能力。
      </p>
      {current?.account ? (
        <>
          <p>
            已连接 {current.site} ·{" "}
            {current.account.display_name || current.account.username}
          </p>
          {current.offline && (
            <p role="status">网站暂不可达，本机功能仍可使用。</p>
          )}
          {current.must_change_password ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void submit("change-password", {
                  current_password: password,
                  new_password: newPassword,
                });
              }}
            >
              <p>网站要求首次修改密码后继续。</p>
              <label>
                当前密码
                <input
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>
              <label>
                新密码
                <input
                  type="password"
                  autoComplete="new-password"
                  required
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                />
              </label>
              <button disabled={busy}>修改密码</button>
            </form>
          ) : (
            <p>
              <a className="creative-primary" href="/creative-workshop">
                打开创意工坊
              </a>
            </p>
          )}
          <button disabled={busy} onClick={() => void submit("logout")}>
            退出网站账号
          </button>
          <p>切换网站或账号，请先退出。已安装的本机副本会保留。</p>
        </>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit("login", {
              site: site ?? current?.site ?? "http://127.0.0.1:3000",
              username,
              password,
            });
          }}
        >
          <label>
            FPL 网站地址
            <input
              type="url"
              required
              value={site ?? current?.site ?? "http://127.0.0.1:3000"}
              onChange={(e) => setSite(e.target.value)}
            />
          </label>
          <label>
            网站账号
            <input
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
          <label>
            网站密码
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <button className="creative-primary" disabled={busy}>
            {busy ? "正在连接…" : "登录 FPL"}
          </button>
          <p>使用已有网站账号；注册及账号管理请前往你的 FPL 网站。</p>
        </form>
      )}
      {(error || status.error) && (
        <p className="creative-error" role="alert">
          {error || String(status.error)}
        </p>
      )}
    </section>
  );
}
