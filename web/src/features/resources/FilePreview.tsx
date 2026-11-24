import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { DetailDialog } from "@shared/ui/DetailDialog";
import { useApiBlobUrl } from "@platform/api/useApiBlobUrl";
import { contentUrl, previewText, type LibraryEntry } from "./fileLibrary";
export function FilePreview({
  file,
  close,
}: {
  file: LibraryEntry;
  close: () => void;
}) {
  const [failed, setFailed] = useState(false);
  const blob = useApiBlobUrl(
    (file.media_kind === "pdf" || file.mime_type === "image/svg+xml") &&
      file.byte_size <= 32 * 1024 * 1024
      ? contentUrl(file)
      : null,
    file.mime_type,
  );
  const text = useQuery({
    queryKey: ["file-text-preview", file.id],
    enabled: file.media_kind === "text",
    queryFn: ({ signal }) => previewText(file, signal),
    retry: false,
  });
  const mediaError = () => setFailed(true);
  return (
    <DetailDialog title={file.name} onClose={close}>
      <div className="file-preview-content">
        {failed || blob.error ? (
          <p role="alert">预览加载失败，可下载原件查看。</p>
        ) : file.mime_type === "image/svg+xml" &&
          file.byte_size > 32 * 1024 * 1024 ? (
          <p>SVG 超过 32 MB，请下载原件查看。</p>
        ) : file.media_kind === "image" ? (
          <img
            src={
              file.mime_type === "image/svg+xml"
                ? blob.url || undefined
                : contentUrl(file)
            }
            alt={file.name}
            onError={mediaError}
          />
        ) : file.media_kind === "video" ? (
          <video
            src={contentUrl(file)}
            controls
            preload="metadata"
            onError={mediaError}
          />
        ) : file.media_kind === "audio" ? (
          <audio
            src={contentUrl(file)}
            controls
            preload="metadata"
            onError={mediaError}
          />
        ) : file.media_kind === "text" ? (
          text.isPending ? (
            <p role="status">正在读取预览…</p>
          ) : text.error ? (
            <p role="alert">{text.error.message}</p>
          ) : (
            <>
              {text.data?.truncated && <p>仅预览前 200 KB，完整内容请下载。</p>}
              <pre>{text.data?.text}</pre>
            </>
          )
        ) : blob.url ? (
          <iframe title={file.name} src={blob.url} />
        ) : (
          <p>
            {file.media_kind === "pdf" && file.byte_size <= 32 * 1024 * 1024
              ? "正在读取 PDF…"
              : "下载原件后查看完整内容。"}
          </p>
        )}
      </div>
      <a className="btn file-preview-download" href={contentUrl(file, true)}>
        <Download size={16} aria-hidden />
        下载原件
      </a>
    </DetailDialog>
  );
}
