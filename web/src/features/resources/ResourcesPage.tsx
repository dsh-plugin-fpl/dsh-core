import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchApiJson, requestApi } from "@platform/api/client";
import { Feedback } from "@shared/ui/RequestFeedback";
import { Plus, File, Download } from "lucide-react";
import { DetailDialog } from "@shared/ui/DetailDialog";
import type { components } from "@generated/api";
type Resource = components["schemas"]["ResourceOut"];
export function Files() {
  const input = useRef<HTMLInputElement>(null);
  const cache = useQueryClient(),
    q = useQuery({
      queryKey: ["core-resources"],
      queryFn: () => fetchApiJson<{ items: Resource[] }>("/v1/resources"),
      refetchInterval: 4000,
    });
  const [preview, setPreview] = useState<Resource | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const upload = async (files: FileList | null) => {
    if (!files) return;
    setBusy(true);
    setError("");
    try {
      for (const file of files) {
        const begin = await fetchApiJson<{ id: string }>("/v1/rpc", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            method: "resource.upload.begin",
            params: {
              name: file.name,
              mime_type: file.type || "application/octet-stream",
              byte_size: file.size,
            },
          }),
        });
        try {
          for (let offset = 0; offset < file.size; offset += 1024 * 1024) {
            const bytes = new Uint8Array(
              await file.slice(offset, offset + 1024 * 1024).arrayBuffer(),
            );
            let binary = "";
            for (const b of bytes) binary += String.fromCharCode(b);
            await requestApi("/v1/rpc", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                method: "resource.upload.append",
                params: { id: begin.id, offset, data_base64: btoa(binary) },
              }),
            });
          }
          await requestApi("/v1/rpc", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              method: "resource.upload.finish",
              params: { id: begin.id },
            }),
          });
        } catch (e) {
          await requestApi("/v1/rpc", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              method: "resource.upload.abort",
              params: { id: begin.id },
            }),
          }).catch(() => {});
          throw e;
        }
      }
      await cache.invalidateQueries({ queryKey: ["core-resources"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="page core-page">
      <header className="page-header">
        <div>
          <h1>文件管理</h1>
          <p>参考素材与生成结果保存在本机。</p>
        </div>
        <button
          type="button"
          className="btn primary"
          disabled={busy}
          onClick={() => input.current?.click()}
        >
          <Plus size={16} />
          <span>{busy ? "上传中…" : "上传文件"}</span>
        </button>
        <input
          ref={input}
          aria-label="选择上传文件"
          type="file"
          multiple
          hidden
          disabled={busy}
          onChange={(e) => void upload(e.target.files)}
        />
      </header>
      <Feedback error={error || q.error?.message} />
      {!q.data?.items.length ? (
        <p className="core-empty">
          还没有文件，上传素材或完成一次生成后会显示在这里。
        </p>
      ) : (
        <div className="core-files">
          {q.data.items.map((r) => (
            <article className="home-panel core-file" key={r.id}>
              <button
                onClick={() => setPreview(r)}
                aria-label={"预览 " + r.name}
              >
                {r.mime_type.startsWith("image/") &&
                r.mime_type !== "image/svg+xml" ? (
                  <img
                    loading="lazy"
                    src={`/v1/resources/${r.id}/content`}
                    alt={r.name}
                  />
                ) : (
                  <File size={40} />
                )}
              </button>
              <strong>{r.name}</strong>
              <small>
                {r.mime_type} · {(r.byte_size / 1024).toFixed(1)} KB
              </small>
              <a
                className="home-text-link"
                href={`/v1/resources/${r.id}/content?download=true`}
              >
                <Download size={14} />
                下载
              </a>
            </article>
          ))}
        </div>
      )}
      {preview && (
        <DetailDialog title={preview.name} onClose={() => setPreview(null)}>
          {preview.mime_type.startsWith("image/") ? (
            <img
              src={`/v1/resources/${preview.id}/content`}
              alt={preview.name}
            />
          ) : preview.mime_type.startsWith("audio/") ? (
            <audio src={`/v1/resources/${preview.id}/content`} controls />
          ) : preview.mime_type.startsWith("video/") ? (
            <video src={`/v1/resources/${preview.id}/content`} controls />
          ) : (
            <p>下载文件后查看内容。</p>
          )}
        </DetailDialog>
      )}
    </section>
  );
}
