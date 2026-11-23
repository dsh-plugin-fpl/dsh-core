import { AgentLogo } from "@features/code-agents/AgentLogo";
import {
  DshPairingModal,
  useDshConnections,
} from "@features/connections/ConnectionsPage";
import { ConfirmDialog } from "@shared/ui/ConfirmDialog";
import { LoadingState } from "@shared/ui/LoadingState";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Network,
  Check,
  ExternalLink,
  Link2,
  Plus,
  Search,
  ShieldCheck,
} from "lucide-react";
import { FeishuMark } from "@shared/ui/FeishuMark";
import { IntegrationModal, PanelMessage, ToggleButton } from "../components";
import { integrationKeys } from "../queryKeys";
import {
  deleteExternalConnection,
  checkExternalConnection,
  createExternalConnection,
  getExternalConnections,
  updateExternalConnection,
  type ExternalConnection,
  type ExternalConnectionInput,
} from "./api";
import "./external-connections.css";

const KEY = integrationKeys.catalog("external-connections");

function ConnectionForm({
  current,
  pending,
  save,
  cancel,
}: {
  current: ExternalConnection | null;
  pending: boolean;
  save: (value: ExternalConnectionInput) => void;
  cancel: () => void;
}) {
  const [name, setName] = useState(current?.name ?? "");
  const [appId, setAppId] = useState(current?.app_id ?? "");
  const [secret, setSecret] = useState("");
  const [enabled, setEnabled] = useState(current?.enabled ?? true);
  return (
    <form
      className="integration-form external-connection-form"
      onSubmit={(e) => {
        e.preventDefault();
        save({
          name,
          app_id: appId,
          app_secret: secret,
          enabled,
          provider: "feishu",
        });
      }}
    >
      <p className="external-connection-intro">
        连接飞书自建应用，供已安装的应用使用文档能力。
      </p>
      <label className="integration-field full">
        <span>连接名称</span>
        <input
          required
          maxLength={120}
          value={name}
          placeholder="例如：团队文章发布"
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label className="integration-field full">
        <span>App ID</span>
        <input
          required
          maxLength={120}
          autoComplete="off"
          value={appId}
          placeholder="cli_…"
          onChange={(e) => setAppId(e.target.value)}
        />
      </label>
      <label className="integration-field full">
        <span>App Secret</span>
        <input
          type="password"
          autoComplete="new-password"
          required={!current}
          maxLength={8192}
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          placeholder={
            current ? "已安全保存，留空保留原凭据" : "粘贴应用的 App Secret"
          }
        />
      </label>
      <aside className="external-connection-guide">
        <div>
          <strong>连接前，完成这三步</strong>
          <a
            href="https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/guide"
            target="_blank"
            rel="noreferrer"
          >
            配置指引 <ExternalLink size={13} aria-hidden />
          </a>
        </div>
        <ol>
          <li>在飞书开放平台创建并发布自建应用</li>
          <li>开通文档读取、编辑与素材上传权限</li>
          <li>为应用授予目标文档的编辑权限</li>
        </ol>
      </aside>
      <p className="external-connection-security">
        <ShieldCheck size={15} aria-hidden />
        凭据加密保存，仅用于当前账号。保存后可检查鉴权。
      </p>
      <div className="external-connection-toggle">
        <div>
          <strong>启用连接</strong>
          <small>停用后不再开始新的同步写入</small>
        </div>
        <ToggleButton value={enabled} onChange={setEnabled} label="启用连接" />
      </div>
      <div className="integration-form-actions external-connection-form-footer">
        <button
          type="button"
          className="btn"
          disabled={pending}
          onClick={cancel}
        >
          取消
        </button>
        <button type="submit" className="btn primary" disabled={pending}>
          {pending ? (
            <LoadingState variant="inline" label="保存中…" />
          ) : (
            "保存连接"
          )}
        </button>
      </div>
    </form>
  );
}

export function ExternalConnectionsPanel({
  canManage,
}: {
  canManage: boolean;
}) {
  const dsh = useDshConnections();
  const [removeDsh, setRemoveDsh] = useState<{
    id: string;
    name: string;
  } | null>(null);
  const query = useQuery({
    queryKey: KEY,
    queryFn: getExternalConnections,
    enabled: canManage,
  });
  const client = useQueryClient();
  const [form, setForm] = useState<{
    current: ExternalConnection | null;
  } | null>(null);
  const [remove, setRemove] = useState<ExternalConnection | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  async function action(work: () => Promise<unknown>) {
    setPending(true);
    setError("");
    try {
      await work();
      await client.invalidateQueries({ queryKey: KEY });
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setPending(false);
    }
  }
  if (!canManage)
    return <PanelMessage>外部连接由账号所有者管理与使用。</PanelMessage>;
  const items = query.data?.items ?? [];
  const bridges = dsh.query.data?.items || [];
  const total = items.length + bridges.length;
  const bridgeCards = bridges.filter((c) =>
    c.name.toLowerCase().includes(search.toLowerCase()),
  );
  const filtered = items.filter((c) =>
    c.name.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <section className="external-connections" aria-label="外部连接">
      <div className="external-connections-heading">
        <div>
          <h2>外部连接</h2>
          <p>统一管理 DSH 配对与外部服务，按需连接已安装应用。</p>
        </div>
        <div className="external-connections-add">
          <button className="btn" onClick={dsh.open}>
            <AgentLogo id="dsh" size={17} />
            连接 DSH
          </button>
          <button
            className="btn primary"
            onClick={() => {
              setError("");
              setForm({ current: null });
            }}
          >
            <Plus size={16} /> 添加飞书连接
          </button>
        </div>
      </div>
      {!!total && (
        <div className="external-connections-toolbar">
          <label className="integration-search">
            <Search size={16} aria-hidden />
            <span className="sr-only">搜索连接</span>
            <input
              aria-label="搜索连接"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索连接…"
            />
          </label>
          <span>{total} 个连接 · 本机所有者</span>
        </div>
      )}
      {query.isPending && (
        <LoadingState label="正在加载连接…" variant="region" />
      )}
      {(error || query.error || dsh.query.error) && (
        <p className="external-connection-error" role="alert">
          {error || query.error?.message || dsh.query.error?.message}
        </p>
      )}
      {!query.isPending && !dsh.query.isPending && total === 0 && (
        <div className="external-connections-empty">
          <span className="external-connections-empty-icon">
            <Link2 size={30} strokeWidth={1.6} aria-hidden />
          </span>
          <h3>添加你的第一个外部连接</h3>
          <p>连接 DSH 或添加外部服务，每个连接单独配置、检查和停用。</p>
          <small>配置保存于本机 · 应用功能由对应插件提供</small>
        </div>
      )}
      <div className="external-connections-list">
        {bridgeCards.map((c) => (
          <article key={"dsh-" + c.id} className="external-connection-card">
            <span className="external-connection-service-icon">
              <AgentLogo id="dsh" size={28} />
            </span>
            <div className="external-connection-card-copy">
              <div className="external-connection-card-heading">
                <strong>{c.name}</strong>
                <span
                  className={
                    "external-connection-status " +
                    (c.connected ? "healthy" : "disabled")
                  }
                >
                  <i />
                  {c.connected ? "已连接" : "未连接"}
                </span>
              </div>
              <p className="external-connection-card-meta">
                <span>DeepSeek Harness</span>
                <span>{c.connected ? "应用与工具" : "等待连接"}</span>
              </p>
            </div>
            <div className="external-connection-actions">
              <button
                className="btn"
                disabled={pending}
                onClick={() => setRemoveDsh(c)}
              >
                撤销配对
              </button>
            </div>
          </article>
        ))}
        {filtered.map((c) => (
          <article key={c.id} className="external-connection-card">
            <FeishuMark size={32} />
            <div className="external-connection-card-copy">
              <div className="external-connection-card-heading">
                <strong>{c.name}</strong>
                <span
                  className={`external-connection-status ${!c.enabled ? "disabled" : c.status}`}
                >
                  <i />
                  {!c.enabled
                    ? "已停用"
                    : c.status === "healthy"
                      ? "连接正常"
                      : c.status === "failed"
                        ? "鉴权失败"
                        : "待检查"}
                </span>
              </div>
              <p className="external-connection-card-meta">
                <span>飞书 · 自建应用</span>
                <span className="external-connection-status-detail">
                  <ShieldCheck size={13} aria-hidden />
                  {c.status_message || "凭据已保存，检查鉴权后即可绑定文档。"}
                </span>
                <span>
                  {c.enabled && c.status === "healthy" ? (
                    <>
                      <Check size={13} aria-hidden />
                      可以在文章中绑定目标
                    </>
                  ) : (
                    c.app_id
                  )}
                </span>
              </p>
            </div>
            <div className="external-connection-actions">
              <button
                className="btn"
                disabled={pending}
                onClick={() => setRemove(c)}
              >
                删除
              </button>
              <button
                className="btn"
                disabled={pending || !c.enabled}
                onClick={() => void action(() => checkExternalConnection(c.id))}
              >
                检查连接
              </button>
              <button
                className="btn"
                disabled={pending}
                onClick={() => {
                  setError("");
                  setForm({ current: c });
                }}
              >
                管理
              </button>
            </div>
          </article>
        ))}
      </div>
      {!!total && !filtered.length && !bridgeCards.length && (
        <PanelMessage>没有找到匹配的连接，试试其他名称。</PanelMessage>
      )}
      {dsh.adding && <DshPairingModal connection={dsh} />}
      {removeDsh && (
        <ConfirmDialog
          open
          title="撤销配对"
          description={"撤销「" + removeDsh.name + "」后，此 DSH 将断开连接。"}
          confirmLabel="撤销"
          pending={pending}
          onCancel={() => setRemoveDsh(null)}
          onConfirm={() =>
            void action(async () => {
              await dsh.revoke(removeDsh.id);
              setRemoveDsh(null);
            })
          }
        />
      )}
      {remove && (
        <ConfirmDialog
          open
          title="删除外部连接"
          pending={pending}
          description={"删除「" + remove.name + "」及其本机凭据副本？"}
          confirmLabel="删除"
          onCancel={() => setRemove(null)}
          onConfirm={() =>
            void action(async () => {
              await deleteExternalConnection(remove);
              setRemove(null);
            })
          }
        />
      )}
      {form && (
        <IntegrationModal
          title={form.current ? "管理飞书连接" : "添加飞书连接"}
          onClose={() => setForm(null)}
          protect
          pending={pending}
          className="external-connection-modal"
          headerIcon={<FeishuMark size={36} />}
        >
          {error && (
            <p className="external-connection-error" role="alert">
              {error}
            </p>
          )}
          <ConnectionForm
            current={form.current}
            pending={pending}
            cancel={() => setForm(null)}
            save={(value) =>
              void action(async () => {
                if (form.current)
                  await updateExternalConnection(form.current, value);
                else await createExternalConnection(value);
                setForm(null);
              })
            }
          />
        </IntegrationModal>
      )}
    </section>
  );
}
