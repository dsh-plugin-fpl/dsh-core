import { useEffect, useState } from "react";
import type { RuntimeStatus } from "../runtime-manager.js";
type Api = (method: any, params?: Record<string, any>) => Promise<any>;
const labels: Record<string, string> = { missing: "未安装", installing: "安装中", awaiting_authorization: "待授权", ready: "就绪", stopped: "已停止", failed: "需要处理", external: "外部服务" };
export function SetupWizard({ api }: { api: Api }) {
  const [services, setServices] = useState<RuntimeStatus[]>([]), [error, setError] = useState(""),
    [busy, setBusy] = useState(""), [selected, setSelected] = useState<string[]>([]), [authorization, setAuthorization] = useState(false);
  const refresh = () => api("setup.status").then(setServices);
  useEffect(() => {
    let active = true;
    const read = () => api("setup.status").then(value => { if (active) setServices(value); }).catch(e => { if (active) setError(e.message); });
    void read(); const timer = setInterval(read, 3000);
    return () => { active = false; clearInterval(timer); };
  }, [api]);
  const action = async (method: string, id = "core") => {
    setBusy(id); setError("");
    try { await api(method, { id }); await refresh(); }
    catch (error) { setError((error as Error).message); }
    finally { setBusy(""); }
  };
  const open = async (method: string) => {
    const popup = window.open("about:blank", "_blank");
    setBusy("authorization"); setError("");
    try {
      const value = await api(method, { ids: selected });
      if (!popup) throw new Error("浏览器阻止了新窗口，请允许后重新点击。");
      popup.opener = null; popup.location.href = value.url;
      if (method === "setup.authorize.begin") setAuthorization(true);
    } catch (error) { popup?.close(); setError((error as Error).message); }
    finally { setBusy(""); }
  };
  const finish = async () => {
    setBusy("authorization"); setError("");
    try {
      const result = await api("setup.authorize.finish");
      if (result.status === "pending") setError("Core 中尚未批准，请完成授权后重试。");
      else { setAuthorization(false); await refresh(); }
    } catch (error) { setError((error as Error).message); }
    finally { setBusy(""); }
  };
  return <section aria-label="FPL 安装向导" style={{ margin: "20px 0", border: "1px solid currentColor", borderRadius: 12, padding: 16 }}>
    <h3>安装与连接</h3>
    <p>先安装 Core，再安装需要的工坊。打开 Core 看板确认账号，然后选择工坊授权。</p>
    <p>安装会联网准备固定 Python 与隔离依赖；缺少适用的 uv 时一并下载到本机运行目录。修复会短暂停止目标服务，业务数据保留在所属服务的数据目录。</p>
    <div style={{ display: "grid", gap: 12 }}>
      {services.map(row => <article key={row.id} data-service={row.id} style={{ borderTop: "1px solid currentColor", paddingTop: 12 }}>
        <strong>{row.name}</strong> <span role="status"> · {labels[row.phase]}</span>
        <p>{row.step}</p>
        {row.error && <p role="alert">{row.error.message}</p>}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {row.mode === "managed" && <>
            <button disabled={!!busy || row.phase === "installing"} onClick={() => void action("setup.install", row.id)}>{row.installed ? "检查 / 修复环境" : "安装运行环境"}</button>
            {row.phase === "installing" && <button disabled={!!busy} onClick={() => void action("setup.cancel", row.id)}>取消安装</button>}
            {row.installed && row.phase !== "installing" && <>
              <button disabled={!!busy} onClick={() => void action("setup.start", row.id)}>启动</button>
              <button disabled={!!busy} onClick={() => void action("setup.stop", row.id)}>停止</button>
            </>}
          </>}
          {row.id !== "core" && row.installed && <label><input type="checkbox" checked={selected.includes(row.id)} onChange={e => setSelected(previous => e.target.checked ? [...previous, row.id] : previous.filter(id => id !== row.id))} /> 加入本次授权</label>}
        </div>
        {!row.ffmpeg && <p>未检测到 FFmpeg / ffprobe。使用 Homebrew 时可运行 <code>brew install ffmpeg</code>，或按 <a href="https://ffmpeg.org/download.html" target="_blank" rel="noreferrer">FFmpeg 官方说明</a> 安装。完成后会自动重新检查。</p>}
        <details><summary>连接信息</summary><p>{row.endpoint} · {row.version || "外部版本"}</p></details>
      </article>)}
    </div>
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 16 }}>
      <button disabled={!!busy} onClick={() => void open("setup.open")}>打开 Core 看板</button>
      <button disabled={!!busy} onClick={() => void open("setup.authorize.begin")}>授权当前 DSH 与所选工坊</button>
      {authorization && <button disabled={!!busy} onClick={() => void finish()}>已在 Core 批准，完成连接</button>}
    </div>
    {error && <p role="alert">{error}</p>}
  </section>;
}
