import { useRef, useState } from "react";
import { fetchApiJson } from "@platform/api/client";
import { Feedback } from "@shared/ui/RequestFeedback";
import type { components } from "@generated/api";
import type { Agent } from "./api";
import { AgentTestResult } from "./AgentTestResult";
import "./agent-test.css";

export function AgentTest({ agent }: { agent: Agent }) {
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [taskId, setTaskId] = useState("");
  const receipt = useRef({ prompt: "", id: "" });
  const available = agent.enabled && agent.available;
  return (
    <section className="agent-test">
      <form
        className="core-form home-panel agent-test-panel"
        onSubmit={async (e) => {
          e.preventDefault();
          if (busy || !available || !prompt.trim()) return;
          const input = prompt.trim();
          if (receipt.current.prompt !== input || !receipt.current.id)
            receipt.current = { prompt: input, id: crypto.randomUUID() };
          setBusy(true);
          setError("");
          try {
            const task = await fetchApiJson<components["schemas"]["TaskOut"]>(
              "/v1/tasks",
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  request_id: receipt.current.id,
                  application_id: "core",
                  kind: "agent",
                  capability: "chat",
                  connection_id: agent.id,
                  prompt: input,
                }),
              },
            );
            setTaskId(task.id);
            receipt.current = { prompt: "", id: "" };
          } catch (cause) {
            setError((cause as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <h2>智能体测试</h2>
        <p>向 {agent.name} 提交一次测试，在下方查看进度与输出。</p>
        {!available && <p role="status">请先启用并连接此智能体。</p>}
        <label>
          测试内容
          <textarea
            value={prompt}
            disabled={busy || !available}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="输入测试任务…"
            rows={3}
          />
        </label>
        <Feedback error={error} />
        <div className="agent-test-actions">
          <button
            className="btn primary"
            disabled={busy || !available || !prompt.trim()}
          >
            {busy ? "正在提交…" : "提交测试"}
          </button>
        </div>
      </form>
      {taskId && <AgentTestResult key={taskId} taskId={taskId} />}
    </section>
  );
}
