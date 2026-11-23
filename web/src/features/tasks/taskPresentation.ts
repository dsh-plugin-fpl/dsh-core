import type { components } from "@generated/api";
type Task = components["schemas"]["TaskOut"];
export const statusNames: Record<Task["status"], string> = {
  queued: "等待执行",
  running: "执行中",
  processing: "生成中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
  recoverable: "可恢复查询",
};
export const isActive = (status: string) =>
  ["queued", "running", "processing"].includes(status);
export function sourceOf(t: Task): {
  id: string;
  label: string;
  href?: string;
} {
  const r = t.request;
  if (r.application_id !== "core") {
    const name = r.application_id.replace(/^b-[0-9a-f-]{36}-/, "");
    return { id: r.application_id, label: `DSH 应用 · ${name}` };
  }
  if (r.kind === "integration")
    return { id: "integrations", label: "集成调用", href: "/integrations" };
  if (r.kind === "agent")
    return {
      id: "agent-test",
      label: "智能体测试",
      href: `/code-agents?agent=${encodeURIComponent(r.connection_id)}`,
    };
  if (r.kind === "local" && r.parameters.action)
    return {
      id: "local-models",
      label: "本地模型管理",
      href: "/model-service",
    };
  const modes: Record<string, string> = {
    image_generation: "image",
    video_generation: "video",
    speech_synthesis: "synthesis",
    speech_recognition: "speech",
    voice: "realtime_voice",
    video_enhancement: "enhancement",
  };
  if (
    r.session_id === "model-test" ||
    r.session_id === "video-enhancement" ||
    r.request_id.startsWith("conversation-")
  ) {
    const conversation =
      r.request_id.startsWith("conversation-") && r.session_id;
    return {
      id: "model-test",
      label: "模型测试",
      href: conversation
        ? `/model-test?conversation=${encodeURIComponent(r.session_id)}`
        : `/model-test?mode=${modes[r.capability] || "chat"}`,
    };
  }
  return { id: "core", label: "本机看板" };
}
export function taskTitle(t: Task) {
  return (
    t.request.prompt?.slice(0, 80) ||
    t.request.model ||
    t.request.connection_id ||
    t.request.capability
  );
}
