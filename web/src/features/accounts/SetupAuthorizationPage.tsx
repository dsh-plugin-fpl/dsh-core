import { useEffect, useState } from "react";
import { requestApi } from "@platform/api/client";
import { useCoreAccount } from "@platform/auth/AccountAccess";

export function SetupAuthorizationPage() {
  const account = useCoreAccount();
  const id = new URLSearchParams(location.search).get("request_id") || "";
  const [request, setRequest] = useState<any>(null), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const load = async () => {
    const response = await requestApi("/v1/setup/authorizations/" + encodeURIComponent(id));
    const value = await response.json();
    if (!response.ok) throw new Error(value.detail || "安装授权不可用，请从 DSH 重新发起");
    setRequest(value);
  };
  useEffect(() => { void load().catch(e => setError(e.message)); }, [id]);
  const approve = async () => {
    setBusy(true); setError("");
    try {
      const response = await requestApi("/v1/setup/authorizations/" + encodeURIComponent(id) + "/approve", { method: "POST" });
      const value = await response.json();
      if (!response.ok) throw new Error(value.detail || "授权失败");
      setRequest(value);
    } catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  };
  return <section className="studio-page" style={{ maxWidth: 720, padding: 24 }}>
    <h1>连接 DSH 与工坊</h1>
    <p>使用当前 Core 账号 <strong>{account.display_name || account.username}</strong> 授权。</p>
    {request && <>
      <p>请求来源：{request.profile}</p>
      <ul>{request.applications.map((app: any) => <li key={app.id}>{app.id} · {app.origin}</li>)}</ul>
      <p>授权后，此 DSH 可调用当前账号的公共模型和所选工坊。私人数据保持当前归属，授权可在账号页撤销。</p>
      {request.status === "approved" ? <p role="status">已批准。请回到 DSH 安装向导完成连接。</p> :
        <button type="button" disabled={busy} onClick={() => void approve()}>{busy ? "正在授权…" : "授权当前账号"}</button>}
    </>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
