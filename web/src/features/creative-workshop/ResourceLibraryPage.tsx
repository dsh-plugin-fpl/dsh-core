import {useEffect, useLayoutEffect, useRef, useState} from 'react';
import {ArrowLeft, ArrowUpRight, Box, Check, Download, FolderGit2, GitBranch, Github, Layers, LayoutTemplate, Package, RefreshCw, Search, Settings2, Sparkles, Workflow, X, type LucideIcon} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import {CustomSelect} from '@shared/ui/CustomSelect';
import {fetchApiJson} from '@platform/api/client';
import {coreApiUrl, coreUrl} from '@platform/embedded';
import {useAccountPermission} from '@platform/auth/AccountAccess';
import './resource-library.css';

type Source = {id: string; name: string; url: string; ref: string};
type Resource = {
  key: string; id: string; name: string; description: string; type: string; version: string;
  origin: string; license: string; status: string; format: string; tags: string[]; targets: string[];
  digest: string; installable: boolean; installed?: boolean; update_available?: boolean;
  previews: {path: string; kind: string; label?: string}[];
};
type Catalog = {source: Source; revision: string; items: Resource[]; errors: {path: string; message: string}[]; refresh_error: unknown; refreshed_at: number};
type Detail = Resource & {content: string; source_url: string; revision: string; dependencies_resolved: Resource[]; preview_status: string};
type Receipt = {id: string; source_id: string; repository: string; revision: string; created_at: string;
  resources: {name: string; kind: string; integration_id: string; version: string}[]};
const labels: Record<string, string> = {skill: 'Skill', integration: '集成配方', template: '模板', workflow: '工作流', preset: '预设', asset: '素材', bundle: '方案包'};
const icons: Record<string, LucideIcon> = {skill: Sparkles, integration: Box, template: LayoutTemplate, workflow: Workflow, preset: Settings2, asset: Layers, bundle: Package};
function ResourceIcon({type}: {type: string}) {
  const Icon = icons[type] ?? Package;
  return <span className={`resource-kind-icon resource-kind-icon--${type}`}><Icon size={20} aria-hidden="true" /></span>;
}
function EmptyState({title, description, action, onAction}: {title: string; description: string; action?: string; onAction?: () => void}) {
  return <div className="resource-empty"><Package size={28} aria-hidden="true" /><h3>{title}</h3><p>{description}</p>{action && <button className="creative-secondary" onClick={onAction}>{action}</button>}</div>;
}
function Skeletons() {
  return <div role="status" aria-label="正在读取资源目录" className="resource-grid resource-skeletons">{Array.from({length: 8}, (_, index) => <div key={index} className="resource-card resource-skeleton" aria-hidden="true"><span /><strong /><p /><small /></div>)}</div>;
}
const message = (value: unknown): string => {
  if (value instanceof Error) return value.message;
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object' && 'message' in value) return String(value.message);
  return '操作失败，请稍后重试';
};
const post = <T,>(path: string, body?: unknown) => fetchApiJson<T>(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body ?? {})});

export function ResourceLibraryPage({view = ''}: {view?: string}) {
  const canManage = useAccountPermission('configuration.manage');
  const [tab, setTab] = useState(view === 'sources' ? 'sources' : 'library');
  const [sources, setSources] = useState<Source[]>([]);
  const [sourceId, setSourceId] = useState('');
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [query, setQuery] = useState('');
  const [type, setType] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [sourcesLoading, setSourcesLoading] = useState(true);
  const [importsLoading, setImportsLoading] = useState(false);
  const [retry, setRetry] = useState(0);
  const [retryTarget, setRetryTarget] = useState('');
  const pageRef = useRef<HTMLElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const listPosition = useRef<{element: HTMLElement; top: number}[]>([]);
  const openedKey = useRef('');
  const restoreList = useRef(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [importKind, setImportKind] = useState('skills');
  const [url, setUrl] = useState('');
  const [ref, setRef] = useState('');
  const [sourceName, setSourceName] = useState('');
  const [names, setNames] = useState<Record<string, string>>({});
  const [configuration, setConfiguration] = useState<Record<string, {command?: string; arguments?: string[]; url?: string}>>({});

  useEffect(() => {if (view === 'sources') setTab('sources');}, [view]);
  useEffect(() => {
    let active = true;
    setSourcesLoading(true);
    fetchApiJson<{items: Source[]}>('/v1/resource-sources').then(result => {
      if (active) {setSources(result.items); setSourceId(previous => result.items.some(source => source.id === previous) ? previous : result.items[0]?.id ?? '');}
    }).catch(cause => {if (active) {setError(message(cause)); setRetryTarget('sources');}})
      .finally(() => active && setSourcesLoading(false));
    return () => {active = false;};
  }, [retry]);
  useEffect(() => {
    if (!sourceId) {setCatalog(null); return;}
    let active = true;
    setLoading(true); setError(''); setRetryTarget(''); setDetail(null); setCatalog(null);
    fetchApiJson<Catalog>(`/v1/resource-sources/${sourceId}/catalog`).then(async result => {
      if (!active) return;
      setCatalog(result);
      if (canManage && Date.now() / 1000 - result.refreshed_at > 300) {
        const refreshed = await post<Catalog>(`/v1/resource-sources/${sourceId}/refresh`);
        if (active) setCatalog(refreshed);
      }
    }).catch(cause => {if (active) {setError(message(cause)); setRetryTarget('catalog');}}).finally(() => active && setLoading(false));
    return () => {active = false;};
  }, [sourceId, canManage, retry]);
  useEffect(() => {
    if (tab !== 'imports') return;
    let active = true;
    setImportsLoading(true);
    fetchApiJson<{items: Receipt[]}>('/v1/resource-library/imports').then(result => active && setReceipts(result.items)).catch(cause => {if (active) {setError(message(cause)); setRetryTarget('imports');}}).finally(() => active && setImportsLoading(false));
    return () => {active = false;};
  }, [tab, retry]);

  useLayoutEffect(() => {
    if (detail) {
      for (let element: HTMLElement | null = pageRef.current; element; element = element.parentElement) element.scrollTop = 0;
      headingRef.current?.focus({preventScroll: true});
    } else if (restoreList.current) {
      for (const {element, top} of listPosition.current) element.scrollTop = top;
      pageRef.current?.querySelector<HTMLButtonElement>(`[data-resource-key="${CSS.escape(openedKey.current)}"]`)?.focus({preventScroll: true});
      restoreList.current = false;
    }
  }, [detail]);
  const changeTab = (value: string) => {setTab(value); setDetail(null); setError(''); setRetryTarget(''); setNotice('');};
  const backToList = () => {restoreList.current = true; setDetail(null);};

  async function action(work: () => Promise<void>) {
    setBusy(true); setError(''); setRetryTarget(''); setNotice('');
    try {await work();} catch (cause) {setError(message(cause));} finally {setBusy(false);}
  }
  const refresh = () => action(async () => {
    const result = await post<Catalog>(`/v1/resource-sources/${sourceId}/refresh`);
    setCatalog(result); setDetail(null);
  });
  const open = (item: Resource) => action(async () => {
    if (!catalog) return;
    const result = await fetchApiJson<Detail>(`/v1/resource-sources/${sourceId}/items/${item.key}?revision=${catalog.revision}`);
    listPosition.current = [];
    for (let element: HTMLElement | null = pageRef.current; element; element = element.parentElement) listPosition.current.push({element, top: element.scrollTop});
    openedKey.current = item.key;
    setDetail(result);
    setNames({}); setConfiguration({});
  });
  const install = () => action(async () => {
    if (!detail) return;
    const receipt = await post<Receipt>('/v1/resource-library/imports', {source_id: sourceId, revision: detail.revision,
      resource_key: detail.key, digest: detail.digest, names, configuration});
    setImportKind(receipt.resources[receipt.resources.length - 1]?.kind || 'skills');
    setNotice(`已导入 ${receipt.resources.length} 项能力。新建能力默认停用，已有副本保留当前配置。`);
    setCatalog(await fetchApiJson<Catalog>(`/v1/resource-sources/${sourceId}/catalog`));
  });
  const visible = catalog?.items.filter(item => (!type || item.type === type) &&
    [item.name, item.description, ...item.tags].join(' ').toLowerCase().includes(query.toLowerCase())) ?? [];
  const fileUrl = (path: string) => detail ? coreApiUrl(`/v1/resource-sources/${sourceId}/items/${detail.key}/file?` + new URLSearchParams({revision: detail.revision, path})) : '';

  return <main className="page creative-page resource-library">
    <header className="creative-header">
      <div><h1>创意工坊</h1><p>从 GitHub 发现可复用资源，导入到当前应用。</p></div>
      <nav className="creative-header-actions" aria-label="资源库导航">
        {([['library', '资源库'], ['imports', '已导入'], ['sources', '来源管理']] as const).map(([value, label]) =>
          <button key={value} className={`creative-tab ${tab === value ? 'active' : ''}`} aria-pressed={tab === value} disabled={busy} onClick={() => {setTab(value); setDetail(null); setError('');}}>{label}</button>)}
      </nav>
    </header>
    {error && <p role="alert" className="creative-error">{error}</p>}
    {notice && !detail && <p role="status" className="resource-notice">{notice} <a href={coreUrl('/integrations?kind=' + importKind)}>打开集成中心</a></p>}
    {tab === 'sources' ? <section className="resource-sources">
      <h2>GitHub 资源来源</h2><p>支持公开的资源仓库、Skill 目录或 SKILL.md 链接。无需网站账号。</p>
      <form onSubmit={event => {event.preventDefault(); void action(async () => {
        const source = await post<Source>('/v1/resource-sources', {url, ref, name: sourceName});
        const result = await fetchApiJson<{items: Source[]}>('/v1/resource-sources');
        setSources(result.items); setSourceId(source.id); setUrl(''); setRef(''); setSourceName(''); setTab('library');
      });}}>
        <label>GitHub 链接<input aria-label="GitHub 链接" placeholder="https://github.com/owner/repository/tree/main/skills/example" value={url} onChange={e => setUrl(e.target.value)} required type="url" maxLength={2048} disabled={!canManage || busy} /></label>
        <div className="resource-source-options"><label>分支或标签（可选）<input value={ref} onChange={e => setRef(e.target.value)} placeholder="默认分支；含 / 时填写完整名称" maxLength={240} disabled={!canManage || busy} /></label>
          <label>来源名称（可选）<input value={sourceName} onChange={e => setSourceName(e.target.value)} maxLength={120} disabled={!canManage || busy} /></label></div>
        <button className="creative-primary" disabled={!canManage || busy}>{busy ? '正在读取 GitHub…' : '添加并浏览'}</button>
      </form>
      <ul className="resource-source-list">{sources.map(source => <li key={source.id}>
        <div><strong>{source.name}</strong><a href={source.url} target="_blank" rel="noreferrer">{source.url}</a>{source.ref && <small>{source.ref}</small>}</div>
        <button className="creative-secondary" disabled={busy} onClick={() => {setSourceId(source.id); setTab('library');}}>浏览</button>
        <button className="creative-text-button" disabled={busy || !canManage} onClick={() => void action(async () => {
          await fetchApiJson(`/v1/resource-sources/${source.id}`, {method: 'DELETE'});
          const result = await fetchApiJson<{items: Source[]}>('/v1/resource-sources');
          setSources(result.items); if (sourceId === source.id) setSourceId(result.items[0]?.id ?? '');
        })}>移除来源</button>
      </li>)}</ul><p className="resource-muted">移除来源会保留已经导入的本地能力和回执。私有仓库暂不支持。</p>
    </section> : tab === 'imports' ? <section><h2>已导入到当前应用</h2>
      {!receipts.length && <p className="creative-empty">还没有导入资源。前往资源库，选择 Skill 或集成配方开始。</p>}
      <ul className="resource-source-list">{[...receipts].reverse().map(receipt => <li key={receipt.id}><div>
        <strong>{receipt.resources.map(r => r.name).join('、')}</strong><small>{receipt.repository} · {receipt.revision.slice(0, 12)}</small>
        <p>{receipt.resources.map(r => `${r.name} · ${r.version.slice(0, 16)}`).join(' / ')}</p>
      </div><a className="creative-secondary" href={coreUrl('/integrations?kind=' + receipt.resources[0]?.kind)}>查看本地能力</a></li>)}</ul>
    </section> : <>
      <div className="creative-toolbar">
        <label>资源来源<select aria-label="资源来源" value={sourceId} disabled={busy} onChange={e => setSourceId(e.target.value)}>{sources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label>
        <button className="creative-secondary" disabled={busy || !sourceId || !canManage} onClick={() => void refresh()}>{busy ? '处理中…' : '刷新来源'}</button>
        <button className="creative-secondary" disabled={busy} onClick={() => setTab('sources')}>从 GitHub 导入</button>
        {catalog && <small className="resource-muted">固定提交 {catalog.revision.slice(0, 12)}</small>}
      </div>
      {catalog?.refresh_error != null && <p role="status" className="resource-notice">{message(catalog.refresh_error)} 当前展示已缓存版本。</p>}
      {catalog?.errors.length ? <details className="resource-notice"><summary>{catalog.errors.length} 项资源未通过校验</summary><ul>{catalog.errors.map(e => <li key={e.path}>{e.path}：{e.message}</li>)}</ul></details> : null}
      {loading && <p role="status">正在读取资源目录…</p>}
      {!sourceId && !loading && <p className="creative-empty">还没有资源来源，请添加公开 GitHub 仓库。</p>}
      {detail ? <article className="resource-detail">
        <button className="creative-text-button" disabled={busy} onClick={() => setDetail(null)}>返回资源列表</button>
        <h2>{detail.name}</h2><p>{detail.description}</p>
        <div className="resource-metadata"><span>{labels[detail.type]}</span><span>版本 {detail.version.slice(0, 16)}</span><span>{detail.license}</span><span>{detail.status === 'draft' ? '草稿 · 仅本地导入' : '已发布'}</span><a href={detail.source_url} target="_blank" rel="noreferrer">查看 GitHub 来源</a></div>
        {detail.license === 'LicenseRef-Unknown' && <p className="resource-muted">来源未注明完整许可，保留原始来源信息；此导入不会发布或上传内容。</p>}
        <p className="resource-muted">{detail.preview_status === 'stale' ? '预览与当前内容不一致，暂不展示。' : detail.preview_status === 'unverified' ? '来源尚未提供预览内容摘要。' : detail.preview_status === 'current' ? '预览已关联当前内容摘要。' : '此资源暂未提供预览。'}</p>
        <p className="resource-muted">{detail.origin && `来源说明：${detail.origin} · `}适用范围：{detail.targets.join('、') || '来源未声明'}（来源声明，业务导入兼容性按实际支持范围校验）</p>
        <div className="resource-previews">{(detail.preview_status === 'stale' ? [] : detail.previews).map(preview => <figure key={preview.path}>
          {preview.kind === 'image' ? <img src={fileUrl(preview.path)} alt={preview.label || detail.name} loading="lazy" /> : preview.kind === 'video' ? <video src={fileUrl(preview.path)} controls /> : preview.kind === 'audio' ? <audio src={fileUrl(preview.path)} controls /> : <a href={fileUrl(preview.path)} download>下载 PDF 预览</a>}
          <figcaption>{preview.label}</figcaption></figure>)}</div>
        {detail.installable ? <section className="resource-install"><h3>导入到本应用</h3><p>导入后保持停用，不会执行脚本、连接 MCP 或调用模型；在集成中心配置后可启用。</p>
          {detail.dependencies_resolved.map(part => <div className="resource-install-part" key={part.key}>
            <label>{part.key === detail.key ? '导入名称（可选）' : `依赖 ${part.name} 的导入名称（可选）`}<input aria-label={`导入名称 ${part.name}`} maxLength={120} placeholder="留空使用资源原名；版本冲突时填写新名称" value={names[part.key] || ''} disabled={busy || !canManage} onChange={e => setNames({...names, [part.key]: e.target.value})} /></label>
            {part.type === 'integration' && <details><summary>本机配置（配方缺少地址或命令时填写）</summary>
              <label>服务地址<input type="url" value={configuration[part.key]?.url ?? ''} disabled={busy || !canManage} onChange={e => setConfiguration({...configuration, [part.key]: {...configuration[part.key], url: e.target.value}})} /></label>
              <label>stdio 命令<input value={configuration[part.key]?.command ?? ''} disabled={busy || !canManage} onChange={e => setConfiguration({...configuration, [part.key]: {...configuration[part.key], command: e.target.value}})} /></label>
              <label>命令参数（每行一个）<textarea disabled={busy || !canManage} onChange={e => setConfiguration({...configuration, [part.key]: {...configuration[part.key], arguments: e.target.value.split('\n').filter(Boolean)}})} /></label>
            </details>}
          </div>)}
          <button className="creative-primary" disabled={busy || !canManage} onClick={() => void install()}>{busy ? '正在导入…' : '一键导入'}</button>
          {notice && <p role="status" className="resource-notice">{notice} <a href={coreUrl('/integrations?kind=' + importKind)}>打开集成中心</a></p>}
        </section> : <p className="resource-notice">此类型暂不支持导入。当前仅支持 Skills、工具 API 和 MCP 配方。</p>}
        <div className="resource-markdown"><ReactMarkdown components={{img: ({src, alt}) => {
          const path = (src || '').replace(/^\.\//, '');
          const allowed = detail.previews.some(p => p.path === path);
          return allowed && detail.preview_status !== 'stale' ? <img src={fileUrl(path)} alt={alt || detail.name} /> : <span>{alt || '图片'}（请查看来源预览）</span>;
        }}}>{detail.content}</ReactMarkdown></div>

      </article> : <>
        <div className="creative-toolbar"><label className="creative-search"><input aria-label="搜索资源" placeholder="搜索名称、用途或关键词" value={query} onChange={e => setQuery(e.target.value)} /></label>
          <select aria-label="资源类型" value={type} onChange={e => setType(e.target.value)}><option value="">全部类型</option>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
        <div className="resource-grid">{visible.map(item => <button className="resource-card" key={item.key} disabled={busy} onClick={() => void open(item)}>
          <div className="resource-metadata"><span>{labels[item.type]}</span><span>{item.installed ? '已导入' : item.update_available ? '有新版本' : item.installable ? '可导入' : '可浏览'}</span></div>
          <strong>{item.name}</strong><p>{item.description}</p><small>{item.version.slice(0, 16)} · {item.license} {item.status === 'draft' ? '· 草稿' : ''}</small>
        </button>)}</div>
        {catalog && !visible.length && <p className="creative-empty">没有匹配的资源。</p>}
      </>}
    </>}
  </main>;
}
