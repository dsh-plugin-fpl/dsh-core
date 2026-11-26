import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchApiJson } from "@platform/api/client";
import { Feedback } from "@shared/ui/RequestFeedback";
import type { components } from "@generated/api";

type Task = components["schemas"]["TaskOut"];
const active = (status: string) =>
  ["queued", "running", "processing"].includes(status);
const statuses: Record<Task["status"], string> = {
  queued: "等待执行",
  running: "执行中",
  processing: "生成中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
  recoverable: "可恢复查询",
};
export function AgentTestResult({ taskId }: { taskId: string }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const query = useQuery({
    queryKey: ["agent-test-result", taskId],
    queryFn: () =>
      fetchApiJson<Task>(`/v1/tasks/${encodeURIComponent(taskId)}`),
    refetchInterval: (q) =>
      q.state.data && active(q.state.data.status) ? 1500 : false,
  });
  const task = query.data;
  const act = async (method: string) => {
    if (pending) return;
    setPending(true);
    setError("");
    try {
      await fetchApiJson("/v1/rpc", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ method, params: { id: taskId } }),
      });
      await query.refetch();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setPending(false);
    }
  };
  return (
    <section className="home-panel agent-test-result" aria-label="本次测试结果">
      <h3>本次测试</h3>
      <Feedback error={query.error?.message || error} />
      {query.isError && (
        <button className="btn" onClick={() => void query.refetch()}>
          重新读取
        </button>
      )}
      {query.isPending && <p role="status">正在读取测试结果…</p>}
      {task && (
        <>
          <p role="status">
            {statuses[task.status]}
            {active(task.status) ? ` ${task.progress}%` : ""}
          </p>
          <div className="agent-test-actions">
            {active(task.status) && (
              <button
                className="btn"
                disabled={pending}
                onClick={() => void act("task.cancel")}
              >
                {pending ? "处理中…" : "停止测试"}
              </button>
            )}
            {task.can_resume && (
              <button
                className="btn"
                disabled={pending}
                onClick={() => void act("task.resume")}
              >
                {pending ? "处理中…" : "恢复查询"}
              </button>
            )}
          </div>
          {task.error && <p role="alert">{task.error.message}</p>}
          <pre>
            {task.text ||
              (task.result ? JSON.stringify(task.result, null, 2) : "尚无输出")}
          </pre>
          {task.result?.resources?.map((r) => (
            <a
              className="btn"
              href={coreApiUrl(`/v1/resources/${r.id}/content?download=true`)}
              key={r.id}
            >
              下载 {r.name}
            </a>
          ))}
        </>
      )}
    </section>
  );
}
import {coreApiUrl} from '@platform/embedded';
