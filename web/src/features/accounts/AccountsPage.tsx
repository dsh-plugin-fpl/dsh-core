import { useEffect, useState, type FormEvent } from "react";
import { fetchApiJson, requestApi } from "@platform/api/client";
import { useCoreAccount, type CoreProfile } from "@platform/auth/AccountAccess";
type Authorization = { id: string; origin: string };
export function AccountsPage() {
  const me = useCoreAccount(),
    [accounts, setAccounts] = useState<CoreProfile[]>([]),
    [apps, setApps] = useState<Authorization[]>([]),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  async function refresh() {
    setApps(
      (
        await fetchApiJson<{ items: Authorization[] }>(
          "/v1/identity/authorizations",
        )
      ).items,
    );
    if (me.role === "admin")
      setAccounts(
        (await fetchApiJson<{ items: CoreProfile[] }>("/v1/identity/accounts"))
          .items,
      );
  }
  useEffect(() => {
    void refresh().catch((e) => setError(e.message));
  }, []);
  async function perform(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }
  async function send(path: string, body: unknown, method = "POST") {
    return requestApi(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }
  function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget,
      body = Object.fromEntries(new FormData(form));
    void perform(async () => {
      await send("/v1/identity/accounts", body);
      form.reset();
      setNotice("账号已创建。");
    });
  }
  return (
    <section className="core-page account-page">
      <h1>账号与授权</h1>
      <p>
        已登录为 {me.display_name} · {me.username}
      </p>
      <p role="alert">{error}</p>
      <p role="status">{notice}</p>
      <h2>已授权的应用</h2>
      <p>撤销后，应用会话与后续交接立即失效。应用中的私人数据会保留。</p>
      <div className="account-list">
        {apps.map((app) => (
          <article className="studio-panel" key={app.id}>
            <strong>{app.id}</strong>
            <span>{app.origin}</span>
            <button
              disabled={busy}
              onClick={() =>
                void perform(() =>
                  requestApi("/v1/identity/authorizations/" + app.id, {
                    method: "DELETE",
                  }),
                )
              }
            >
              撤销授权
            </button>
          </article>
        ))}
        {!apps.length && <p>尚未授权应用。</p>}
      </div>
      {me.role === "admin" && (
        <>
          <h2>账号管理</h2>
          <p>管理员可创建和禁用账号；私人项目与模型配置仍按账号隔离。</p>
          <div className="account-list">
            {accounts.map((account) => (
              <article className="studio-panel" key={account.id}>
                <strong>{account.display_name}</strong>
                <span>
                  {account.username} ·{" "}
                  {account.role === "admin" ? "管理员" : "普通账号"} ·{" "}
                  {account.active ? "已启用" : "已禁用"}
                </span>
                {!account.legacy && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void perform(() =>
                        send(
                          "/v1/identity/accounts/" + account.id,
                          {
                            active: !account.active,
                            expected_revision: account.revision,
                          },
                          "PATCH",
                        ),
                      )
                    }
                  >
                    {account.active ? "禁用" : "启用"}
                  </button>
                )}
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    const form = e.currentTarget,
                      password = new FormData(form).get("password");
                    void perform(async () => {
                      await send(
                        "/v1/identity/accounts/" + account.id + "/password",
                        { password },
                      );
                      form.reset();
                      if (account.id === me.id) location.assign("/login");
                      else setNotice("密码已重置，原会话已撤销。");
                    });
                  }}
                >
                  <label>
                    新密码
                    <input
                      name="password"
                      type="password"
                      autoComplete="new-password"
                      required
                      minLength={12}
                      maxLength={256}
                    />
                  </label>
                  <button disabled={busy}>设置密码并撤销会话</button>
                </form>
              </article>
            ))}
          </div>
          <h2>创建账号</h2>
          <form onSubmit={create} className="account-form">
            <label>
              用户名
              <input
                name="username"
                required
                pattern="[A-Za-z0-9._-]{3,64}"
                autoComplete="off"
              />
            </label>
            <label>
              显示名称
              <input name="display_name" required maxLength={120} />
            </label>
            <label>
              初始密码
              <input
                name="password"
                required
                type="password"
                minLength={12}
                maxLength={256}
                autoComplete="new-password"
              />
            </label>
            <button disabled={busy}>创建普通账号</button>
          </form>
        </>
      )}
    </section>
  );
}
