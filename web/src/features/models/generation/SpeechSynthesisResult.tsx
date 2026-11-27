import { coreProduct, coreUrl } from "@platform/embedded";
import { productApi } from "@features/resources/productFiles";
import { LoadingScene } from "@shared/ui/LoadingScene";
import { LoadingState } from "@shared/ui/LoadingState";
import { useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  Download,
  FolderPlus,
  Pause,
  Play,
  TriangleAlert,
} from "lucide-react";
import { useApiBlobUrl } from "@platform/api/useApiBlobUrl";
import { useAccountPermission } from "@platform/auth/AccountAccess";
import {
  saveSpeechTask,
  type InferenceTask,
} from "@features/models/localModelApi";
import { apiErrorMessage } from "@platform/api/errors";

import {
  speechTaskStatus,
  speechAudioTime,
  speechCloningSummary,
} from "./speechSynthesisPresentation";

export function SpeechSynthesisResult({
  task,
  autoPlay = false,
}: {
  task: InferenceTask;
  autoPlay?: boolean;
}) {
  const player = useRef<HTMLAudioElement>(null);
  const saving = useRef(false);
  const canWrite = useAccountPermission("content.write");
  const audio = useApiBlobUrl(
    task.result_available
      ? `/v1/model-inference/tasks/${task.id}/content`
      : null,
  );
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [position, setPosition] = useState(0);
  const [savePending, setSavePending] = useState(false);
  const [saved, setSaved] = useState(false);
  const [project, setProject] = useState("");
  const [projects, setProjects] = useState<
    { id: string; title?: string; name?: string }[]
  >([]);
  useEffect(() => {
    if (coreProduct())
      void productApi<{
        items: { id: string; title?: string; name?: string }[];
      }>("file-projects")
        .then((x) => setProjects(x.items))
        .catch((e) => setError(String(e)));
  }, []);
  const [error, setError] = useState("");
  useEffect(() => {
    setSaved(false);
    setError("");
  }, [task.id]);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (autoPlay && audio.url)
      void player.current
        ?.play()
        .catch(() => setError("请点击播放按钮试听，或下载 WAV。"));
  }, [audio.url, autoPlay]);
  async function save() {
    if (!canWrite || saving.current) return;
    saving.current = true;
    setSavePending(true);
    setError("");
    try {
      if (coreProduct() && !project) throw new Error("请选择目标项目");
      const resource = await saveSpeechTask(task.id);
      if (coreProduct())
        await productApi("project-files", {
          project_id: project,
          source: "core",
          source_id: resource.id,
        });
      if (mounted.current) setSaved(true);
    } catch (cause) {
      if (mounted.current) setError(apiErrorMessage(cause, "保存失败"));
    } finally {
      saving.current = false;
      if (mounted.current) setSavePending(false);
    }
  }
  const completed = task.status === "completed";
  const failed = task.status === "failed";
  return (
    <article
      className={`speech-result${failed ? " is-failed" : ""}`}
      aria-label="选中的生成结果"
    >
      <div
        className={`speech-task-status${completed ? " is-completed" : failed ? " is-failed" : ""}`}
        role="status"
      >
        {completed ? (
          <CheckCircle2 size={17} aria-hidden />
        ) : failed ? (
          <TriangleAlert size={17} aria-hidden />
        ) : (
          <LoadingScene />
        )}
        {speechTaskStatus(task.status)}
      </div>
      <p className="speech-result-text" tabIndex={0}>
        {String(task.request_payload.text ?? "") || "本次生成未提供文字预览"}
      </p>
      {!!speechCloningSummary(task.request_payload) && (
        <p className="speech-help" aria-label="本次合成设置">
          {speechCloningSummary(task.request_payload)}
        </p>
      )}
      {!completed && !failed && (
        <p className="speech-help">
          生成完成后可在此试听。首次加载模型可能较慢。
        </p>
      )}
      {failed && (
        <p className="speech-feedback is-error" role="alert">
          {task.error_message || "生成未完成，请检查输入和模型状态。"}
        </p>
      )}
      {task.result_available && (
        <>
          <audio
            hidden
            ref={player}
            src={audio.url || undefined}
            preload="metadata"
            onLoadedMetadata={(event) =>
              setDuration(
                Number.isFinite(event.currentTarget.duration)
                  ? event.currentTarget.duration
                  : 0,
              )
            }
            onDurationChange={(event) =>
              setDuration(
                Number.isFinite(event.currentTarget.duration)
                  ? event.currentTarget.duration
                  : 0,
              )
            }
            onTimeUpdate={(event) =>
              setPosition(event.currentTarget.currentTime)
            }
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
            onError={() => {
              if (audio.url) setError("音频无法播放，请下载 WAV 后重试。");
            }}
          />
          <div className="speech-player">
            <button
              type="button"
              className="speech-play-button"
              aria-label={playing ? "暂停" : "播放语音"}
              disabled={!audio.url || audio.loading}
              onClick={() => {
                setError("");
                if (playing) player.current?.pause();
                else
                  void player.current
                    ?.play()
                    .catch(() => setError("无法播放音频，请下载后重试"));
              }}
            >
              {audio.loading ? (
                <LoadingScene />
              ) : playing ? (
                <Pause size={24} aria-hidden />
              ) : (
                <Play size={24} aria-hidden />
              )}
            </button>
            <span className="speech-time">{speechAudioTime(position)}</span>
            <div className="speech-seek">
              <span
                style={{
                  width: `${duration > 0 ? (position / duration) * 100 : 0}%`,
                }}
              />
              <input
                type="range"
                aria-label="播放位置"
                min={0}
                max={duration || 1}
                step={0.1}
                value={Math.min(position, duration || 0)}
                disabled={!audio.url || duration <= 0}
                onChange={(event) => {
                  const next = Number(event.target.value);
                  if (player.current) player.current.currentTime = next;
                  setPosition(next);
                }}
              />
            </div>
            <span className="speech-time">
              {duration > 0 ? speechAudioTime(duration) : "--:--"}
            </span>
          </div>
          <div className="speech-result-actions">
            {audio.url && (
              <a
                className="btn"
                href={audio.url}
                download={task.result_filename || "speech.wav"}
              >
                <Download size={16} aria-hidden />
                下载 WAV
              </a>
            )}
            {coreProduct() && (
              <label>
                保存到项目{" "}
                <select
                  aria-label="保存到项目"
                  value={project}
                  disabled={savePending || saved}
                  onChange={(e) => setProject(e.target.value)}
                >
                  <option value="">选择项目</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.title || p.name || p.id}
                    </option>
                  ))}
                </select>
                {!projects.length && (
                  <a href={coreProduct()!.projectsUrl}>创建项目</a>
                )}
              </label>
            )}
            <button
              className="btn"
              type="button"
              disabled={
                !canWrite ||
                savePending ||
                saved ||
                (!!coreProduct() && !project)
              }
              onClick={() => void save()}
            >
              <FolderPlus size={16} aria-hidden />
              {savePending ? (
                <LoadingState variant="inline" label="正在保存…" />
              ) : saved ? (
                coreProduct() ? (
                  "已保存到项目"
                ) : (
                  "已保存到文件库"
                )
              ) : coreProduct() ? (
                "保存到项目"
              ) : (
                "保存到文件库"
              )}
            </button>
          </div>
          {!canWrite && (
            <p className="speech-help">保存文件需要内容写入权限。</p>
          )}
        </>
      )}
      {completed && !task.result_available && (
        <p className="speech-help">生成结果暂不可用，请刷新记录核对。</p>
      )}
      {audio.error && (
        <p className="speech-feedback is-error" role="alert">
          音频读取失败：{audio.error.message}
        </p>
      )}
      {error && (
        <p className="speech-feedback is-error" role="alert">
          {error}
        </p>
      )}
      {saved && (
        <p className="speech-feedback is-success" role="status">
          {coreProduct() ? (
            <a
              href={coreUrl(
                "/files?project=" + encodeURIComponent(project),
              )}
            >
              已保存到项目 · 查看文件
            </a>
          ) : (
            "已保存到文件库"
          )}
        </p>
      )}
    </article>
  );
}
