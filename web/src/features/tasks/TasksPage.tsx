import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchApiJson, requestApi } from "@platform/api/client";
import { Feedback } from "@shared/ui/RequestFeedback";
import { Square } from "lucide-react";
import { CustomSelect } from "@shared/ui/CustomSelect";
import { DetailDialog } from "@shared/ui/DetailDialog";
import type { components } from "@generated/api";
import { sourceOf, statusNames, isActive, taskTitle } from "./taskPresentation";
import "./tasks.css";

type Task = components["schemas"]["TaskOut"];
export function TaskList({ compact = false }: { compact?: boolean }) {
  const cache = useQueryClient();
  const q = useQuery({
    queryKey: ["core-tasks"],
    queryFn: () => fetchApiJson<{ items: Task[] }>("/v1/tasks"),
    refetchInterval: 1500,
  });
  const [selectedId, setSelectedId] = useState<string | null>(() =>
    compact ? null : new URLSearchParams(location.search).get("task"),
  );
  const [source, setSource] = useState("all");
  const [status, setStatus] = useState("all");
  const [error, setError] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const items = q.data?.items ?? [];
  const detail = items.find((t) => t.id === selectedId);
  const sources = new Map(
    items.map((t) => {
      const origin = sourceOf(t);
      return [origin.id, origin.label];
    }),
  );
  const filtered = items.filter(
    (t) =>
      (source === "all" || sourceOf(t).id === source) &&
      (status === "all" ||
        (status === "active"
          ? isActive(t.status)
          : status === "attention"
            ? ["failed", "interrupted", "recoverable"].includes(t.status)
            : t.status === status)),
  );
  const visible = compact ? filtered.slice(0, 5) : filtered;
  const action = async (t: Task, method: string) => {
    if (pending) return;
    setPending(t.id);
    try {
      setError("");
      await requestApi("/v1/rpc", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ method, params: { id: t.id } }),
      });
      await cache.invalidateQueries({ queryKey: ["core-tasks"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(null);
    }
  };
  const actions = (t: Task) => (
    <div className="run-actions">
      {isActive(t.status) && (
        <button
          className="btn"
          disabled={pending !== null}
          onClick={() => void action(t, "task.cancel")}
          aria-label={`停止 ${taskTitle(t)}`}
        >
          <Square size={12} aria-hidden />
          {pending === t.id ? "处理中…" : "停止"}
        </button>
      )}
      {t.can_resume && (
        <button
          className="btn"
          disabled={pending !== null}
          onClick={() => void action(t, "task.resume")}
        >
          {pending === t.id ? "处理中…" : "恢复查询"}
        </button>
      )}
    </div>
  );
  return (
    <>
      <Feedback error={q.error?.message} />
      {!compact && items.length > 0 && (
        <div className="run-filters" role="group" aria-label="运行记录筛选">
          <CustomSelect
            ariaLabel="运行来源"
            value={source}
            onChange={setSource}
            options={[
              { value: "all", label: "全部来源" },
              ...Array.from(sources, ([value, label]) => ({ value, label })),
            ]}
          />
          <CustomSelect
            ariaLabel="运行状态"
            value={status}
            onChange={setStatus}
            options={[
              { value: "all", label: "全部状态" },
              { value: "active", label: "进行中" },
              { value: "attention", label: "需要处理" },
              { value: "completed", label: "已完成" },
              { value: "cancelled", label: "已取消" },
            ]}
          />
          <span role="status">共 {filtered.length} 条运行记录</span>
        </div>
      )}
      {!detail && <Feedback error={error} />}
      {q.isPending ? (
        <p className="core-empty">正在读取运行记录…</p>
      ) : !items.length && !q.error ? (
        <div className="core-empty">
          <p>暂无运行记录</p>
          <p>模型测试和已连接应用调用 AI 能力后，会在这里显示进度与结果。</p>
        </div>
      ) : !filtered.length && items.length > 0 ? (
        <div className="core-empty">
          <p>没有符合筛选条件的运行记录。</p>
          <button
            className="btn"
            onClick={() => {
              setSource("all");
              setStatus("all");
            }}
          >
            清除筛选
          </button>
        </div>
      ) : (
        <div className="core-task-list">
          {visible.map((t) => (
            <article key={t.id} className="core-task-row">
              <div>
                <button
                  className="core-link"
                  onClick={() => setSelectedId(t.id)}
                >
                  {taskTitle(t)}
                </button>
                <small>
                  {sourceOf(t).label} ·{" "}
                  {t.request.model || t.request.connection_id || "默认模型"} ·{" "}
                  {new Date(t.created_at * 1000).toLocaleString()}
                </small>
              </div>
              <span className={"core-status " + t.status}>
                {statusNames[t.status]}
                {["running", "processing"].includes(t.status)
                  ? ` ${t.progress}%`
                  : ""}
              </span>
              {!compact && actions(t)}
            </article>
          ))}
        </div>
      )}
      {detail && (
        <DetailDialog
          title="运行详情"
          onClose={() => {
            setSelectedId(null);
            if (!compact && new URLSearchParams(location.search).has("task")) {
              const url = new URL(location.href);
              url.searchParams.delete("task");
              history.replaceState(null, "", url.pathname + url.search);
            }
          }}
        >
          <dl className="run-metadata">
            <dt>任务</dt>
            <dd>{taskTitle(detail)}</dd>
            <dt>状态</dt>
            <dd>{statusNames[detail.status]}</dd>
            <dt>来源</dt>
            <dd>{sourceOf(detail).label}</dd>
            <dt>模型 / 智能体</dt>
            <dd>
              {detail.request.model ||
                detail.request.connection_id ||
                "默认模型"}
            </dd>
            <dt>开始时间</dt>
            <dd>{new Date(detail.created_at * 1000).toLocaleString()}</dd>
            <dt>运行编号</dt>
            <dd>{detail.id}</dd>
          </dl>
          <div className="run-actions">
            {sourceOf(detail).href && (
              <a className="btn" href={sourceOf(detail).href}>
                打开来源
              </a>
            )}
            {actions(detail)}
          </div>
          <Feedback error={error} />
          {detail.error && (
            <div className="run-error" role="alert">
              <strong>{detail.error.message}</strong>
              <small>{detail.error.code}</small>
            </div>
          )}
          {detail.request.prompt && (
            <>
              <h3>输入</h3>
              <pre>{detail.request.prompt}</pre>
            </>
          )}
          <h3>输出</h3>
          <pre>
            {detail.text ||
              (detail.result
                ? JSON.stringify(detail.result, null, 2)
                : "尚无输出")}
          </pre>
          {detail.result?.resources?.map((r) => (
            <a
              className="btn"
              href={`/v1/resources/${r.id}/content?download=true`}
              key={r.id}
            >
              下载 {r.name}
            </a>
          ))}
        </DetailDialog>
      )}
    </>
  );
}
export function Tasks() {
  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>运行记录</h1>
          <p>查看各应用的执行进度与结果，处理失败或中断的运行。</p>
        </div>
      </header>
      <TaskList />
    </section>
  );
}
