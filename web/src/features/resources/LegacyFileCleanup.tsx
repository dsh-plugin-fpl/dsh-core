import { useEffect, useRef, useState } from "react";
import { coreProduct, coreUrl } from "@platform/embedded";
import { useAccountPermission } from "@platform/auth/AccountAccess";
import { fileRequest, loadProductFiles, productApi, type ProductFile } from "./productFiles";
import "./legacy-files.css";

export function LegacyFileCleanup() {
  const writable = useAccountPermission("content.write");
  const [data, setData] = useState<Awaited<ReturnType<typeof loadProductFiles>>>({ items: [], projects: [], warnings: [] });
  const [project, setProject] = useState(""), [selected, setSelected] = useState<string[]>([]), [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState(""), [version, setVersion] = useState(0);
  const running = useRef(false), mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError("");
    void loadProductFiles(fileRequest, "", controller.signal).then(setData).catch(e => { if (!controller.signal.aborted) setError(String(e)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [version]);
  const mapped = new Set(data.items.flatMap(item => item.receipts.map(receipt => receipt.source + ":" + receipt.source_id)));
  const items = data.items.filter(item => !item.receipts.length && !mapped.has(item.key) && item.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  async function archive() {
    if (running.current || !project || !writable) return;
    running.current = true; setBusy(true); setError(""); setNotice("");
    let count = 0;
    try {
      for (const item of items.filter(item => selected.includes(item.key))) {
        await productApi("project-files", { project_id: project, source: item.source, source_id: item.id }); count++;
      }
    } catch (e) { if (mounted.current) setError(String(e)); }
    finally {
      running.current = false;
      if (mounted.current) { setBusy(false); setSelected([]); setNotice(`已归档 ${count} 个文件；原件与原引用保留。`); setVersion(v => v + 1); }
    }
  }
  return <section className="page legacy-files-page"><h1>旧文件整理</h1>
    <p>将旧文件关联到对应项目。归档不会移动或删除原件；无法确认归属的文件可以保留在这里或下载导出。</p>
    <p><a href={coreProduct()?.projectsUrl}>返回项目列表</a> · <a href={coreUrl("/storage")}>设置</a></p>
    <label>目标项目<select aria-label="目标项目" value={project} disabled={busy} onChange={e => setProject(e.target.value)}><option value="">选择项目</option>{data.projects.map(p => <option key={p.id} value={p.id}>{p.title || p.name || p.id}</option>)}</select></label>
    <label>搜索文件<input aria-label="搜索文件" value={query} onChange={e => setQuery(e.target.value)} /></label>
    {writable && <button disabled={!project || !selected.length || busy || loading} onClick={() => void archive()}>{busy ? "正在归档…" : "归档到项目"}</button>}
    {project && <p><a href={coreUrl("/files?project=" + encodeURIComponent(project))}>在文件库查看项目附件</a></p>}
    {(error || data.warnings.length > 0) && <p role="alert">{error || data.warnings.join("；")} <button onClick={() => setVersion(v => v + 1)}>重试</button></p>}
    {notice && <p role="status">{notice}</p>}
    {loading ? <p role="status">正在读取文件…</p> : <ul>{items.map((item: ProductFile) => <li key={item.key}>
      {writable && <input aria-label={"选择 " + item.name} type="checkbox" checked={selected.includes(item.key)} disabled={busy} onChange={e => setSelected(values => e.target.checked ? [...values, item.key] : values.filter(key => key !== item.key))} />}
      <span className="legacy-file-name">{item.name}</span><a href={item.content_url} target="_blank" rel="noreferrer">预览 / 下载</a>
    </li>)}</ul>}
    {!loading && !items.length && !error && <p>没有待整理的旧文件。</p>}
  </section>;
}
