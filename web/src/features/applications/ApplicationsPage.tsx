import { useState } from "react";
import { Search, Plus, Package, ExternalLink, ArrowLeft } from "lucide-react";
import { DetailDialog } from "@shared/ui/DetailDialog";
import { Feedback } from "@shared/ui/RequestFeedback";
import {
  useApplications,
  APPLICATION_CATEGORIES,
  categoryLabel,
  statusLabel,
} from "./catalog";
import "./applications.css";
export function ApplicationsPage({
  navigate,
  selected,
}: {
  navigate: (p: string) => void;
  selected?: string;
}) {
  const query = useApplications();
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [guide, setGuide] = useState(false);
  const applications = query.data || [];
  const app = applications.find((app) => app.id === selected);
  const filtered = applications.filter(
    (app) =>
      (category === "all" || app.category === category) &&
      [app.name, app.description, app.package_name, ...app.capabilities]
        .join(" ")
        .toLocaleLowerCase()
        .includes(search.toLocaleLowerCase()),
  );
  return (
    <section className="page applications-page core-page">
      <header className="page-header">
        <div>
          <h1>{selected ? app?.name || "应用暂不可用" : "应用"}</h1>
          <p>通过 DeepSeek Harness 插件安装和管理你的应用。</p>
        </div>
        <button
          className="btn primary"
          type="button"
          onClick={() => setGuide(true)}
        >
          <Plus size={16} aria-hidden />
          <span>安装应用</span>
        </button>
      </header>
      <Feedback error={query.error?.message} />
      {selected ? (
        <>
          <button
            className="home-text-link core-back"
            onClick={() => navigate("/apps")}
          >
            <ArrowLeft size={16} />
            全部应用
          </button>
          {app ? (
            <article className="home-panel core-application-detail">
              <app.iconComponent size={40} aria-hidden />
              <h2>{app.name}</h2>
              <p>{app.description || "由已启用的 DSH 插件提供。"}</p>
              <dl>
                <dt>状态</dt>
                <dd>{statusLabel(app)}</dd>
                <dt>分类</dt>
                <dd>{categoryLabel(app.category)}</dd>
                <dt>来源</dt>
                <dd>
                  {app.package_name || app.bridge_name}
                  {app.version && " · " + app.version}
                </dd>
              </dl>
              {app.status === "ready" ? (
                <a
                  className="btn primary"
                  href={app.url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  打开应用
                  <ExternalLink size={16} />
                </a>
              ) : (
                <p>
                  {app.status === "offline"
                    ? "连接 DeepSeek Harness 后即可打开此应用。"
                    : "应用已登记，正在等待插件提供页面入口。"}
                </p>
              )}
            </article>
          ) : (
            <p className="core-empty">
              此应用未启用或已卸载。请检查 DSH 中的插件状态。
            </p>
          )}
        </>
      ) : (
        <>
          <form
            className="applications-search"
            role="search"
            onSubmit={(e) => e.preventDefault()}
          >
            <Search aria-hidden />
            <input
              aria-label="搜索应用"
              placeholder="搜索应用名称、能力或关键词…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <button>搜索</button>
          </form>
          <div className="applications-categories" aria-label="应用分类">
            {APPLICATION_CATEGORIES.map((c) => (
              <button
                key={c.value}
                className={category === c.value ? "active" : ""}
                aria-pressed={category === c.value}
                onClick={() => setCategory(c.value)}
              >
                {c.label}
              </button>
            ))}
          </div>
          <p className="core-application-count">{applications.length} 个应用</p>
          <div className="application-grid">
            {filtered.map((app) => (
              <article key={app.id} className="application-card">
                <span className="application-icon">
                  {app.brandImage ? (
                    <img src={app.brandImage} alt="" />
                  ) : (
                    <app.iconComponent aria-hidden />
                  )}
                </span>
                <button
                  className="application-card-open"
                  onClick={() => navigate(app.path)}
                  aria-label={"打开" + app.name}
                >
                  {app.name}
                </button>
                <small className={"core-application-status " + app.status}>
                  {statusLabel(app)}
                </small>
                <div className="application-copy">
                  <small>
                    {app.description || app.package_name || app.bridge_name}
                  </small>
                  <span className="application-capabilities">
                    {app.capabilities.map((c) => (
                      <em key={c}>{c}</em>
                    ))}
                  </span>
                </div>
              </article>
            ))}
          </div>
          {!filtered.length && (
            <div className="application-empty">
              <Package aria-hidden />
              <strong>
                {applications.length ? "没有找到匹配的应用" : "还没有安装应用"}
              </strong>
              <span>
                {applications.length
                  ? "试试其他分类或关键词。"
                  : "在 DSH 中安装并启用应用插件，连接后会自动显示在这里。"}
              </span>
              <button className="btn" onClick={() => setGuide(true)}>
                查看安装方式
              </button>
            </div>
          )}
        </>
      )}
      {guide && (
        <DetailDialog title="安装应用" onClose={() => setGuide(false)}>
          <ol className="core-install-steps">
            <li>
              打开 DeepSeek Harness 的插件管理，安装并启用需要的应用插件。
            </li>
            <li>
              在 DSH「设置 → FPL Core 连接」中，使用本看板生成的配对凭据连接。
            </li>
            <li>
              支持 FPL
              应用入口的插件会自动出现在首页和应用列表中。停用或卸载后，入口随之移除。
            </li>
          </ol>
          <button
            className="btn primary"
            onClick={() => {
              setGuide(false);
              navigate("/integrations?kind=external-connections");
            }}
          >
            管理 DSH 连接
          </button>
        </DetailDialog>
      )}
    </section>
  );
}
