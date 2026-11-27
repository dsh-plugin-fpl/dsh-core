import { useEffect, useRef, useState } from "react";
import { useAccountPermission } from "@platform/auth/AccountAccess";
import { fileRequest, loadProductFiles, productApi, type ProductFile } from "./productFiles";
import { FileItems } from "./FileItems";
import { FilePreview } from "./FilePreview";
import { uploadFile, type LibraryEntry } from "./fileLibrary";
import { Files } from "./ResourcesPage";
import "./styles/asset-browser-foundation.css";
import "./styles/asset-browser-components.css";
import "./styles/file-library.css";
import "./resources.css";
import "./product-library.css";

export function ProductLibrary() {
  const writable = useAccountPermission("content.write");
  const [project, setProject] = useState(new URLSearchParams(location.search).get("project") || "");
  const [target, setTarget] = useState(project);
  const [data, setData] = useState<Awaited<ReturnType<typeof loadProductFiles>>>({ items: [], projects: [], warnings: [] });
  const [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [version, setVersion] = useState(0), [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const [query, setQuery] = useState(""), [source, setSource] = useState("all"), [kind, setKind] = useState("all"), [sort, setSort] = useState("name"), [page, setPage] = useState(0), [grid, setGrid] = useState(false);
  const [preview, setPreview] = useState<LibraryEntry | null>(null), [folders, setFolders] = useState(false);
  const operation = useRef(false), mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(""); setData({ items: [], projects: [], warnings: [] });
    void loadProductFiles(fileRequest, project, controller.signal).then(value => { if (!controller.signal.aborted) setData(value); })
      .catch(e => { if (!controller.signal.aborted) setError(String(e)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [project, version]);
  useEffect(() => { setPage(0); setPreview(null); }, [project, query, source, kind, sort]);
  const run = async (action: () => Promise<void>) => {
    if (operation.current || !writable) return;
    operation.current = true; setBusy(true); setError(""); setNotice("");
    try { await action(); if (mounted.current) setVersion(v => v + 1); }
    catch (e) { if (mounted.current) setError(String(e)); }
    finally { operation.current = false; if (mounted.current) setBusy(false); }
  };
  const selectProject = (id: string) => {
    setProject(id); setTarget(id);
    const url = new URL(location.href); if (id) url.searchParams.set("project", id); else url.searchParams.delete("project");
    history.replaceState(null, "", url);
  };
  const items = data.items.filter(item => (source === "all" || item.source === source) && (kind === "all" || item.media_kind === kind) && item.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()))
    .sort((a, b) => sort === "size" ? b.byte_size - a.byte_size || a.key.localeCompare(b.key) : a.name.localeCompare(b.name, "zh-CN") || a.key.localeCompare(b.key));
  const pages = Math.max(1, Math.ceil(items.length / 48)), currentPage = Math.min(page, pages - 1);
  const entries: LibraryEntry[] = items.slice(currentPage * 48, (currentPage + 1) * 48).map(item => ({ ...item, id: item.key, sha256: "", application_id: "", revision: 1 }));
  if (folders) return <><button className="btn" onClick={() => { setFolders(false); setVersion(v => v + 1); }}>返回文件库</button><Files /></>;
  return <section className="file-management-page product-file-library" aria-label="文件库">
    <header><h1>文件库</h1><p>管理素材与附件，关联项目后可在编辑器中选用。</p></header>
    <div className="file-browser-filters">
      <label>项目范围<select aria-label="项目范围" value={project} disabled={busy} onChange={e => selectProject(e.target.value)}><option value="">全部文件</option>{data.projects.map(p => <option key={p.id} value={p.id}>{p.title || p.name || p.id}</option>)}</select></label>
      <label>搜索文件<input aria-label="搜索文件" value={query} onChange={e => setQuery(e.target.value)} /></label>
      <label>来源<select aria-label="文件来源" value={source} onChange={e => setSource(e.target.value)}><option value="all">全部来源</option><option value="core">文件与附件</option><option value="application">工坊素材</option></select></label>
      <label>类型<select aria-label="文件类型" value={kind} onChange={e => setKind(e.target.value)}>{[["all","全部类型"],["image","图片"],["audio","音频"],["video","视频"],["pdf","PDF"],["text","文本"],["file","其他文件"]].map(([value,label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>排序<select aria-label="文件排序" value={sort} onChange={e => setSort(e.target.value)}><option value="name">按名称</option><option value="size">按大小</option></select></label>
      <button className="btn" aria-pressed={grid} onClick={() => setGrid(!grid)}>{grid ? "列表视图" : "网格视图"}</button>
      <button className="btn" disabled={busy} onClick={() => setVersion(v => v + 1)}>刷新</button>
    </div>
    {writable && <div className="product-file-actions">
      <label className="btn">{project ? "上传项目附件" : "上传文件"}<input aria-label={project ? "上传项目附件" : "上传文件"} type="file" multiple disabled={busy} onChange={e => {
        const files = Array.from(e.target.files || []); e.target.value = "";
        const destination = project;
        void run(async () => {
          const failures: string[] = [];
          for (const file of files) try {
            const resource = await uploadFile(file, null);
            if (destination) await productApi("project-files", { project_id: destination, source: "core", source_id: resource.id });
          } catch (e) { failures.push(`${file.name}：${String(e)}；已上传原件可在全部文件中继续关联。`); }
          if (mounted.current) { setNotice(`已处理 ${files.length - failures.length} 个文件。`); if (failures.length) setError(failures.join("；")); }
        });
      }} /></label>
      {project ? <button className="btn" disabled={busy} onClick={() => { setProject(""); const url = new URL(location.href); url.searchParams.delete("project"); history.replaceState(null, "", url); }}>关联已有文件</button> : <label>关联目标项目<select aria-label="关联目标项目" disabled={busy} value={target} onChange={e => setTarget(e.target.value)}><option value="">选择项目</option>{data.projects.map(p => <option key={p.id} value={p.id}>{p.title || p.name || p.id}</option>)}</select></label>}
      <button className="btn" disabled={busy} onClick={() => setFolders(true)}>管理文件夹与归档</button>
    </div>}
    {(error || data.warnings.length > 0) && <p role="alert">{error || data.warnings.join("；")} <button className="btn" disabled={busy} onClick={() => setVersion(v => v + 1)}>重试</button></p>}
    {notice && <p role="status">{notice}</p>}{busy && <p role="status">正在处理文件…</p>}
    {loading ? <p role="status">正在读取文件…</p> : <>
      <FileItems folders={[]} files={entries} view={grid ? "grid" : "list"} openFolder={() => {}} preview={setPreview} edit={() => {}} renderActions={entry => {
        const item = items.find(x => x.key === entry.id)!;
        const associated = item.receipts.some(r => r.project_id === target);
        return <div className="product-file-row-actions"><a href={item.content_url} target="_blank" rel="noreferrer">下载原件</a>
          {item.receipts.length > 0 && <small>{item.receipts.map(r => data.projects.find(p => p.id === r.project_id)?.title || data.projects.find(p => p.id === r.project_id)?.name || r.project_id).join("、")}</small>}
          {writable && !project && target && <button disabled={busy || associated} onClick={() => void run(async () => {
            await productApi("project-files", { project_id: target, source: item.source, source_id: item.id }); setNotice("已关联到项目。");
          })}>{associated ? "已关联" : "关联到项目"}</button>}
          {writable && project && item.receipts.filter(r => r.project_id === project).map(receipt => <button key={receipt.source + receipt.source_id} disabled={busy} onClick={() => void run(async () => {
            await productApi("project-files", { project_id: project, source: receipt.source, source_id: receipt.source_id }, "DELETE"); setNotice("已移除项目关联，原件保留。");
          })}>移除项目关联</button>)}
        </div>;
      }} />
      {!items.length && <p>没有匹配的文件。</p>}
      {pages > 1 && <nav aria-label="文件分页"><button disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button> {currentPage + 1} / {pages} <button disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>下一页</button></nav>}
    </>}
    {preview && <FilePreview key={preview.id} file={preview} close={() => setPreview(null)} />}
  </section>;
}
