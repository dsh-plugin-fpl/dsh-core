import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Download,
  FileText,
  Folder,
  FolderInput,
  MoreHorizontal,
  Music,
  Pencil,
  Trash2,
  Video,
} from "lucide-react";
import { useApiBlobUrl } from "@platform/api/useApiBlobUrl";
import { formatFileSize } from "@shared/lib/fileSize";
import {
  contentUrl,
  kindOptions,
  updatedLabel,
  type Editor,
  type LibraryEntry,
  type LibraryFolder,
  type LibraryItem,
} from "./fileLibrary";

export function FileMenu({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null),
    trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const close = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);
  return (
    <div
      ref={root}
      className={`file-item-menu${open ? " open" : ""}`}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          setOpen(false);
          trigger.current?.focus();
          event.stopPropagation();
        }
        if (event.key === "Tab") setOpen(false);
        if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
          event.preventDefault();
          if (!open) {
            setOpen(true);
            return;
          }
          const items = Array.from(
            root.current!.querySelectorAll<HTMLElement>('[role="menuitem"]'),
          );
          const index = items.indexOf(document.activeElement as HTMLElement);
          items[
            event.key === "Home"
              ? 0
              : event.key === "End"
                ? items.length - 1
                : (index + (event.key === "ArrowUp" ? -1 : 1) + items.length) %
                  items.length
          ]?.focus();
        }
      }}
    >
      <button
        ref={trigger}
        type="button"
        className="file-item-menu-trigger"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <MoreHorizontal aria-hidden />
      </button>
      {open && (
        <div
          className="file-item-menu-popover"
          role="menu"
          aria-label={label}
          onClick={() => {
            setOpen(false);
            trigger.current?.focus();
          }}
        >
          {children}
        </div>
      )}
    </div>
  );
}
export function FileThumbnail({ file }: { file: LibraryEntry }) {
  const [failed, setFailed] = useState(false);
  const svg = useApiBlobUrl(
    file.mime_type === "image/svg+xml" && file.byte_size <= 2 * 1024 * 1024
      ? contentUrl(file)
      : null,
    "image/svg+xml",
  );
  if (
    file.media_kind === "image" &&
    !failed &&
    (file.mime_type !== "image/svg+xml" || svg.url)
  )
    return (
      <img
        loading="lazy"
        src={
          file.mime_type === "image/svg+xml"
            ? svg.url || undefined
            : contentUrl(file)
        }
        alt=""
        onError={() => setFailed(true)}
      />
    );
  const Icon =
    file.media_kind === "video"
      ? Video
      : file.media_kind === "audio"
        ? Music
        : FileText;
  return <Icon aria-hidden />;
}
function ItemActions({
  item,
  edit,
  preview,
}: {
  item: LibraryItem;
  edit: (editor: Editor) => void;
  preview: () => void;
}) {
  return (
    <FileMenu label={`${item.value.name} 的更多操作`}>
      <button role="menuitem" onClick={preview}>
        {item.type === "folder" ? (
          <Folder aria-hidden />
        ) : (
          <FileText aria-hidden />
        )}
        {item.type === "folder" ? "打开文件夹" : "预览"}
      </button>
      {item.type === "file" && (
        <a role="menuitem" href={contentUrl(item.value, true)}>
          <Download aria-hidden />
          下载
        </a>
      )}
      <button role="menuitem" onClick={() => edit({ action: "rename", item })}>
        <Pencil aria-hidden />
        重命名
      </button>
      <button role="menuitem" onClick={() => edit({ action: "move", item })}>
        <FolderInput aria-hidden />
        移动到…
      </button>
      <button
        role="menuitem"
        className="danger"
        onClick={() => edit({ action: "delete", item })}
      >
        <Trash2 aria-hidden />
        {item.type === "folder" ? "删除空文件夹" : "从文件管理移除"}
      </button>
    </FileMenu>
  );
}
export function FileItems({
  folders,
  files,
  view,
  openFolder,
  preview,
  edit,
}: {
  folders: LibraryFolder[];
  files: LibraryEntry[];
  view: string;
  openFolder: (id: string) => void;
  preview: (file: LibraryEntry) => void;
  edit: (editor: Editor) => void;
}) {
  return (
    <div className={`file-items ${view}`}>
      <div className="file-folder-collection">
        {folders.map((folder) => (
          <article className="file-folder-item" key={folder.id}>
            <button
              type="button"
              className="file-item-main"
              aria-label={`打开文件夹 ${folder.name}`}
              onClick={() => openFolder(folder.id)}
            >
              <span className="file-item-preview folder">
                <Folder aria-hidden />
              </span>
              <span className="file-item-copy">
                <strong title={folder.name}>{folder.name}</strong>
                <small>文件夹</small>
              </span>
            </button>
            <div className="file-item-footer">
              <time dateTime={folder.updated_at || undefined}>
                {updatedLabel(folder.updated_at)}
              </time>
            </div>
            <ItemActions
              item={{ type: "folder", value: folder }}
              edit={edit}
              preview={() => openFolder(folder.id)}
            />
          </article>
        ))}
      </div>
      <div className="file-asset-collection">
        {files.map((file) => (
          <article className="file-asset-item" key={file.id}>
            <button
              type="button"
              className="file-item-main"
              aria-label={`预览 ${file.name}`}
              onClick={() => preview(file)}
            >
              <span className={`file-item-preview ${file.media_kind}`}>
                <FileThumbnail file={file} />
              </span>
              <span className="file-item-copy">
                <strong title={file.name}>{file.name}</strong>
                <span className="file-item-tags">
                  <small>
                    {kindOptions.find((kind) => kind.value === file.media_kind)
                      ?.label || "文件"}
                  </small>
                  <small>
                    {file.name.includes(".")
                      ? file.name.split(".").pop()?.slice(0, 8).toUpperCase()
                      : "FILE"}
                  </small>
                </span>
                <small className="file-item-size">
                  {formatFileSize(file.byte_size)}
                </small>
              </span>
            </button>
            <div className="file-item-footer">
              <time dateTime={file.updated_at || undefined}>
                {updatedLabel(file.updated_at)}
              </time>
            </div>
            <ItemActions
              item={{ type: "file", value: file }}
              edit={edit}
              preview={() => preview(file)}
            />
          </article>
        ))}
      </div>
    </div>
  );
}
