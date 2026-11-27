import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Check,
  Download,
  Eye,
  FileArchive,
  FileText,
  Folder,
  FolderInput,
  FolderPlus,
  Grid2X2,
  List,
  MoreHorizontal,
  Music,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  Video,
  X,
} from "lucide-react";
import type {
  FileBrowserAdapter,
  FileBrowserItem,
  FileBrowserLocation,
  FileBrowserPage,
  FileBrowserProject,
} from "./fileBrowserTypes";
import foundation from "./styles/asset-browser-foundation.css?inline";
import components from "./styles/asset-browser-components.css?inline";
import library from "./styles/file-library.css?inline";
import styles from "./file-browser.css?inline";

const empty: FileBrowserPage = { items: [], breadcrumbs: [] };
const kindNames: Record<string, string> = {
  image: "图片",
  video: "视频",
  audio: "音频",
  pdf: "PDF",
  text: "文本",
  file: "文件",
};
function sizeLabel(size: number) {
  return size < 1024
    ? `${size} B`
    : size < 1024 ** 2
      ? `${(size / 1024).toFixed(1)} KB`
      : size < 1024 ** 3
        ? `${(size / 1024 ** 2).toFixed(1)} MB`
        : `${(size / 1024 ** 3).toFixed(1)} GB`;
}
export function fileAccepted(item: FileBrowserItem, accept = "") {
  return (
    !accept ||
    accept.split(",").some((raw) => {
      const token = raw.trim().toLowerCase();
      return token.startsWith(".")
        ? item.name.toLowerCase().endsWith(token)
        : token.endsWith("/*")
          ? item.mimeType.toLowerCase().startsWith(token.slice(0, -1))
          : item.mimeType.toLowerCase() === token;
    })
  );
}
function Menu({ label, children }: { label: string; children: ReactNode }) {
  const root = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node))
        root.current?.removeAttribute("open");
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, []);
  return (
    <details
      ref={root}
      className="browser-menu"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          root.current?.removeAttribute("open");
          root.current?.querySelector("summary")?.focus();
        }
        if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
          event.preventDefault();
          root.current?.setAttribute("open", "");
          const items = Array.from(
            root.current?.querySelectorAll<HTMLElement>(
              '[role="menuitem"]:not(:disabled)',
            ) || [],
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
      <summary aria-label={label} title={label}>
        <MoreHorizontal aria-hidden size={16} />
        <span>{label}</span>
      </summary>
      <div
        className="browser-menu-content"
        role="menu"
        aria-label={label}
        onClick={() => root.current?.removeAttribute("open")}
      >
        {children}
      </div>
    </details>
  );
}
function Thumbnail({ item }: { item: FileBrowserItem }) {
  const [failed, setFailed] = useState(false),
    [visible, setVisible] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "200px" },
    );
    if (root.current) observer.observe(root.current);
    return () => observer.disconnect();
  }, []);
  const Icon =
    item.type === "folder"
      ? Folder
      : item.kind === "audio"
        ? Music
        : item.kind === "video"
          ? Video
          : FileText;
  return (
    <span
      ref={root}
      className={`file-item-preview ${item.type === "folder" ? "folder" : ""}`}
    >
      {item.type === "file" &&
      visible &&
      !failed &&
      (item.thumbnailUrl || item.kind === "image") ? (
        <img
          loading="lazy"
          src={item.thumbnailUrl || item.contentUrl}
          alt=""
          onError={() => setFailed(true)}
        />
      ) : item.type === "file" &&
        item.kind === "video" &&
        visible &&
        !failed ? (
        <video
          src={item.contentUrl + "#t=0.1"}
          muted
          preload="metadata"
          onError={() => setFailed(true)}
        />
      ) : (
        <Icon aria-hidden />
      )}
    </span>
  );
}
function Modal({
  title,
  close,
  children,
  className = "",
}: {
  title: string;
  close: () => void;
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null),
    trigger = useRef(document.activeElement as HTMLElement | null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => {
      dialog?.close();
      trigger.current?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={`fpl-browser-dialog ${className}`}
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
    >
      <header>
        <strong>{title}</strong>
        <button type="button" aria-label={`关闭${title}`} onClick={close}>
          <X size={18} />
        </button>
      </header>
      {children}
    </dialog>
  );
}
function Preview({
  adapter,
  item,
  close,
}: {
  adapter: FileBrowserAdapter;
  item: FileBrowserItem;
  close: () => void;
}) {
  const [url, setUrl] = useState(""),
    [error, setError] = useState(""),
    [text, setText] = useState("");
  useEffect(() => {
    if (
      !["pdf", "text"].includes(item.kind) &&
      item.mimeType !== "image/svg+xml"
    )
      return;
    const controller = new AbortController();
    let owned = "";
    void (async () => {
      if (
        (item.kind === "pdf" || item.mimeType === "image/svg+xml") &&
        item.size > 32 * 1024 ** 2
      )
        throw Error("文件超过预览大小限制，请下载原件查看。");
      const response = await adapter.request(item.contentUrl!, {
        signal: controller.signal,
        ...(item.kind === "text"
          ? { headers: { Range: "bytes=0-204799" } }
          : {}),
      });
      if (!response.ok) throw Error(`预览读取失败（${response.status}）`);
      if (item.kind === "text") {
        const reader = response.body?.getReader();
        const chunks: Uint8Array[] = [];
        let length = 0;
        if (reader)
          try {
            while (length < 204800) {
              const { value, done } = await reader.read();
              if (done) break;
              const part = value.subarray(0, 204800 - length);
              chunks.push(part);
              length += part.length;
            }
          } finally {
            await reader.cancel();
          }
        const bytes = new Uint8Array(length);
        let offset = 0;
        chunks.forEach((chunk) => {
          bytes.set(chunk, offset);
          offset += chunk.length;
        });
        if (!controller.signal.aborted)
          setText(new TextDecoder().decode(bytes));
      } else {
        const blob = await response.blob();
        controller.signal.throwIfAborted();
        owned = URL.createObjectURL(new Blob([blob], { type: item.mimeType }));
        setUrl(owned);
      }
    })().catch((error) => {
      if (!controller.signal.aborted) setError(String(error));
    });
    return () => {
      controller.abort();
      if (owned) URL.revokeObjectURL(owned);
    };
  }, [adapter, item]);
  return (
    <Modal title={item.name} close={close} className="fpl-browser-preview">
      <div className="browser-preview-content">
        {error ? (
          <p role="alert">{error}</p>
        ) : item.kind === "image" ? (
          <img
            src={item.mimeType === "image/svg+xml" ? url : item.contentUrl}
            alt={item.name}
            onError={() => setError("预览加载失败，可下载原件查看。")}
          />
        ) : item.kind === "video" ? (
          <video
            controls
            autoPlay={false}
            preload="metadata"
            src={item.contentUrl}
            onError={() => setError("预览加载失败，可下载原件查看。")}
          />
        ) : item.kind === "audio" ? (
          <audio
            controls
            preload="metadata"
            src={item.contentUrl}
            onError={() => setError("预览加载失败，可下载原件查看。")}
          />
        ) : item.kind === "pdf" ? (
          url ? (
            <iframe src={url} title={item.name} />
          ) : (
            <p role="status">正在读取预览…</p>
          )
        ) : item.kind === "text" ? (
          <>
            {item.size > 204800 && <p>仅预览前 200 KB。</p>}
            <pre>{text || "正在读取预览…"}</pre>
          </>
        ) : (
          <p>下载原件后查看完整内容。</p>
        )}
      </div>
      <footer>
        <span>
          {kindNames[item.kind]} · {sizeLabel(item.size)}
        </span>
        <a href={item.contentUrl} download={item.name}>
          <Download size={15} />
          下载原件
        </a>
      </footer>
    </Modal>
  );
}
type Editor = {
  action: "create" | "rename" | "move" | "remove";
  item?: FileBrowserItem;
};
export type SharedFileBrowserProps = {
  adapter: FileBrowserAdapter;
  projectId?: string;
  onProjectChange?: (id: string) => void;
  writable?: boolean;
  picker?: boolean;
  compact?: boolean;
  accept?: string;
  multiple?: boolean;
  disabled?: boolean;
  onSelect?: (
    items: FileBrowserItem[],
    signal: AbortSignal,
  ) => Promise<unknown> | void;
  onChoose?: (file: File, signal: AbortSignal) => Promise<unknown> | void;
  close?: () => void;
  initialSource?: "library" | "project" | "cross";
};
export function SharedFileBrowser({
  adapter,
  projectId,
  onProjectChange,
  writable = true,
  picker,
  compact,
  accept,
  multiple,
  disabled,
  onSelect,
  onChoose,
  close,
  initialSource,
}: SharedFileBrowserProps) {
  const [location, setLocation] = useState<FileBrowserLocation>({
      projectId: picker && initialSource !== "project" ? undefined : projectId,
    }),
    [data, setData] = useState(empty),
    [projects, setProjects] = useState<FileBrowserProject[]>([]);
  const [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [loadError, setLoadError] = useState(""),
    [projectError, setProjectError] = useState(""),
    [notice, setNotice] = useState(""),
    [version, refresh] = useState(0);
  const [query, setQuery] = useState(""),
    [kind, setKind] = useState("all"),
    [sort, setSort] = useState("updated");
  const [view, setView] = useState(() => {
    try {
      return localStorage.getItem(`${adapter.id}:file-view`) === "list"
        ? "list"
        : "grid";
    } catch {
      return "grid";
    }
  });
  const [preview, setPreview] = useState<FileBrowserItem>(),
    [editor, setEditor] = useState<Editor>(),
    [editorName, setEditorName] = useState(""),
    [moveFolders, setMoveFolders] = useState<FileBrowserItem[]>([]),
    [moveTarget, setMoveTarget] = useState("");
  const [busy, setBusy] = useState(false),
    [progress, setProgress] = useState(""),
    [selected, setSelected] = useState<string[]>([]),
    [tab, setTab] = useState(
      initialSource || (picker ? "library" : projectId ? "project" : "library"),
    ),
    [page, setPage] = useState(0);
  const locked = useRef(false),
    transfer = useRef<AbortController | null>(null),
    mounted = useRef(true);
  const input = useRef<HTMLInputElement>(null),
    zip = useRef<HTMLInputElement>(null),
    directory = useRef<HTMLInputElement>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      transfer.current?.abort();
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setProjectError("");
    void adapter
      .projects(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setProjects(value);
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setProjectError(`项目列表读取失败：${String(error)}`);
      });
    return () => controller.abort();
  }, [adapter, version]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setLoadError("");
    setData(empty);
    setSelected([]);
    void adapter
      .browse(location, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setLoadError(String(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [adapter, location, version]);
  useEffect(() => {
    setPage(0);
  }, [query, kind, sort, location]);
  useEffect(() => {
    if (editor?.action !== "move" || !adapter.folders) return;
    const controller = new AbortController();
    setMoveFolders([]);
    void adapter
      .folders(
        { source: editor.item?.source === "core" ? "core" : "application" },
        controller.signal,
      )
      .then((folders) => {
        if (!controller.signal.aborted) setMoveFolders(folders);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(String(error));
      });
    return () => controller.abort();
  }, [adapter, editor]);
  async function run(
    action: (signal: AbortSignal) => Promise<unknown>,
    success: string,
    reload = true,
  ) {
    if (locked.current || disabled) return;
    locked.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    const controller = new AbortController();
    transfer.current = controller;
    try {
      await action(controller.signal);
      if (mounted.current && !controller.signal.aborted) {
        setNotice(success);
        setEditor(undefined);
      }
    } catch (error) {
      if (mounted.current)
        setError(
          controller.signal.aborted
            ? "操作已取消，已完成的文件会保留。"
            : String(error),
        );
    } finally {
      locked.current = false;
      if (mounted.current) {
        setBusy(false);
        setProgress("");
        if (reload) refresh((v) => v + 1);
      }
    }
  }
  const navigate = (next: FileBrowserLocation) => {
    if (locked.current) return;
    setLocation(next);
    setQuery("");
    setError("");
    setNotice("");
    setSelected([]);
  };
  const openFolder = (item?: FileBrowserItem) =>
    navigate({
      projectId: location.projectId,
      ...(item
        ? {
            folderId: item.id,
            source: item.source === "core" ? "core" : "application",
          }
        : {}),
    });
  const files = data.items.filter(
    (item) =>
      item.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()) &&
      (item.type === "folder" ||
        ((kind === "all" || item.kind === kind) && fileAccepted(item, accept))),
  );
  files.sort((a, b) =>
    a.type !== b.type
      ? a.type === "folder"
        ? -1
        : 1
      : sort === "name"
        ? a.name.localeCompare(b.name, "zh-CN")
        : sort === "size"
          ? b.size - a.size || a.key.localeCompare(b.key)
          : (b.updatedAt || "").localeCompare(a.updatedAt || "") ||
            a.name.localeCompare(b.name, "zh-CN"),
  );
  const pages = Math.max(1, Math.ceil(files.length / 100)),
    currentPage = Math.min(page, pages - 1),
    shown = files.slice(currentPage * 100, (currentPage + 1) * 100);
  const selectedItems = data.items.filter((item) =>
    selected.includes(item.key),
  );
  const availableProjects = projects.filter(
    (project) => project.id !== projectId,
  );
  const changeTab = (value: "library" | "project" | "cross") => {
    setTab(value);
    navigate({
      projectId:
        value === "project"
          ? projectId
          : value === "cross"
            ? availableProjects[0]?.id
            : undefined,
    });
  };
  const startEdit = (action: Editor["action"], item?: FileBrowserItem) => {
    setError("");
    setEditorName(item?.name || "");
    setMoveTarget("");
    setEditor({ action, item });
  };
  const choose = () =>
    void run(
      async (signal) => {
        if (onSelect) await onSelect(selectedItems, signal);
        else if (onChoose)
          for (const item of selectedItems) {
            signal.throwIfAborted();
            const response = await adapter.request(item.contentUrl!, {
              signal,
            });
            if (!response.ok) throw Error(`文件读取失败（${response.status}）`);
            const blob = await response.blob();
            signal.throwIfAborted();
            await onChoose(
              new File([blob], item.name, { type: item.mimeType }),
              signal,
            );
            if (mounted.current)
              setSelected((keys) => keys.filter((key) => key !== item.key));
          }
        signal.throwIfAborted();
        close?.();
      },
      "素材已选用。",
      false,
    );
  const downloadFolder = (target: FileBrowserLocation) =>
    void run(async (signal) => {
      const blob = await adapter.exportFolder!(target, signal);
      const url = URL.createObjectURL(blob),
        link = document.createElement("a");
      link.href = url;
      link.download = target.folderId
        ? "files.zip"
        : target.source === "core"
          ? "files-and-attachments.zip"
          : "workshop-files.zip";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    }, "目录已导出。");
  const toolbar = (
    <>
      <div className="file-browser-toolbar">
        <div className="file-location">
          <nav className="file-breadcrumb" aria-label="文件夹路径">
            <button type="button" onClick={() => openFolder()}>
              <span role="heading" aria-level={1}>
                {picker ? (compact ? "全部文件" : "选择素材") : "文件管理"}
              </span>
            </button>
            {data.breadcrumbs.map((item) => (
              <span key={item.key}>
                <i aria-hidden>/</i>
                <button onClick={() => openFolder(item)}>{item.name}</button>
              </span>
            ))}
          </nav>
          {!picker && (
            <select
              aria-label="项目范围"
              value={location.projectId || ""}
              disabled={busy}
              onChange={(event) => {
                navigate({ projectId: event.target.value || undefined });
                onProjectChange?.(event.target.value);
              }}
            >
              <option value="">文件库</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.title}
                </option>
              ))}
            </select>
          )}
          {!loading && (
            <small>
              {files.filter((x) => x.type === "folder").length} 个文件夹 ·{" "}
              {files.filter((x) => x.type === "file").length} 个文件
            </small>
          )}
        </div>
        <div className="file-primary-actions">
          {writable && !picker && (
            <>
              <button
                type="button"
                className="primary"
                disabled={busy}
                onClick={() => input.current?.click()}
              >
                <Upload aria-hidden />
                上传文件
              </button>
              {!location.projectId && adapter.createFolder && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => startEdit("create")}
                >
                  <FolderPlus aria-hidden />
                  新建文件夹
                </button>
              )}
              <Menu label="更多操作">
                {adapter.exportFolder && !location.projectId && (
                  <button
                    role="menuitem"
                    disabled={busy}
                    onClick={() => downloadFolder(location)}
                  >
                    <Download size={15} />
                    {location.source || location.folderId
                      ? "导出当前目录"
                      : "导出工坊文件"}
                  </button>
                )}
                {adapter.exportFolder &&
                  !location.projectId &&
                  !location.source &&
                  !location.folderId &&
                  data.items.some((item) => item.source === "core") && (
                    <button
                      role="menuitem"
                      disabled={busy}
                      onClick={() => downloadFolder({ source: "core" })}
                    >
                      <Download size={15} />
                      导出文件与附件
                    </button>
                  )}
                {adapter.importArchive && !location.projectId && (
                  <button
                    role="menuitem"
                    disabled={busy}
                    onClick={() => zip.current?.click()}
                  >
                    <FileArchive size={15} />
                    导入 ZIP
                  </button>
                )}
                {adapter.importDirectory && !location.projectId && (
                  <button
                    role="menuitem"
                    disabled={busy}
                    onClick={() => directory.current?.click()}
                  >
                    <FolderInput size={15} />
                    导入文件夹
                  </button>
                )}
                <button
                  role="menuitem"
                  disabled={busy}
                  onClick={() => refresh((v) => v + 1)}
                >
                  <RefreshCw size={15} />
                  刷新
                </button>
              </Menu>
            </>
          )}
          {picker && close && (
            <button aria-label="关闭素材选择" onClick={close}>
              <X />
            </button>
          )}
        </div>
      </div>
      {picker && (
        <div
          className="browser-source-tabs"
          role="tablist"
          aria-label="素材来源"
        >
          <button
            role="tab"
            aria-selected={tab === "library"}
            disabled={busy}
            onClick={() => changeTab("library")}
          >
            文件库
          </button>
          {projectId && (
            <button
              role="tab"
              aria-selected={tab === "project"}
              disabled={busy}
              onClick={() => changeTab("project")}
            >
              本项目
            </button>
          )}
          <button
            role="tab"
            aria-selected={tab === "cross"}
            disabled={busy || !availableProjects.length}
            onClick={() => changeTab("cross")}
          >
            其他项目
          </button>
          {tab === "cross" && (
            <select
              aria-label="来源项目"
              value={location.projectId || ""}
              disabled={busy}
              onChange={(event) => navigate({ projectId: event.target.value })}
            >
              {availableProjects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.title}
                </option>
              ))}
            </select>
          )}
        </div>
      )}
      <div className="file-browser-filters">
        <label>
          <Search aria-hidden />
          <input
            aria-label="搜索文件或文件夹"
            placeholder="搜索文件或文件夹"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <select
          aria-label="文件类型"
          value={kind}
          onChange={(event) => setKind(event.target.value)}
        >
          <option value="all">全部类型</option>
          {Object.entries(kindNames).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <select
          aria-label="文件排序"
          value={sort}
          onChange={(event) => setSort(event.target.value)}
        >
          <option value="updated">最近更新</option>
          <option value="name">按名称</option>
          <option value="size">按大小</option>
        </select>
        <div className="file-view-toggle" role="group" aria-label="文件视图">
          {(
            [
              ["grid", Grid2X2, "卡片视图"],
              ["list", List, "列表视图"],
            ] as const
          ).map(([value, Icon, label]) => (
            <button
              type="button"
              key={value}
              aria-label={label}
              aria-pressed={view === value}
              className={view === value ? "active" : ""}
              onClick={() => {
                setView(value);
                try {
                  localStorage.setItem(`${adapter.id}:file-view`, value);
                } catch {
                  /* Storage may be unavailable. */
                }
              }}
            >
              <Icon aria-hidden />
            </button>
          ))}
        </div>
      </div>
    </>
  );
  function itemCard(item: FileBrowserItem) {
    const selectedItem = selected.includes(item.key);
    return (
      <article
        key={item.key}
        data-file-key={item.key}
        className={`${item.type === "folder" ? "file-folder-item" : "file-asset-item"}${selectedItem ? " is-selected" : ""}`}
      >
        <button
          type="button"
          className="file-item-main"
          aria-label={
            item.type === "folder"
              ? `打开文件夹 ${item.name}`
              : `${picker ? "选择" : "预览"} ${item.name}`
          }
          aria-pressed={
            picker && item.type === "file" ? selectedItem : undefined
          }
          disabled={busy || disabled}
          onClick={() =>
            item.type === "folder"
              ? openFolder(item)
              : picker
                ? setSelected((keys) =>
                    multiple
                      ? keys.includes(item.key)
                        ? keys.filter((key) => key !== item.key)
                        : [...keys, item.key]
                      : [item.key],
                  )
                : setPreview(item)
          }
        >
          <Thumbnail item={item} />
          <span className="file-item-copy">
            <strong title={item.name}>{item.name}</strong>
            <span className="file-item-tags">
              <small>
                {item.type === "folder" ? "文件夹" : kindNames[item.kind]}
              </small>
              {item.type === "file" && (
                <small>
                  {item.name.includes(".")
                    ? item.name.split(".").pop()?.toUpperCase()
                    : item.mimeType.split("/").pop()}
                </small>
              )}
            </span>
            {item.type === "file" && (
              <span className="file-item-size">{sizeLabel(item.size)}</span>
            )}
          </span>
          {selectedItem && (
            <span className="browser-selection-check">
              <Check size={14} />
            </span>
          )}
        </button>
        <div className="file-item-footer">
          <small>
            {item.updatedAt
              ? new Date(item.updatedAt).toLocaleDateString("zh-CN")
              : ""}
          </small>
          {item.type === "file" && <small>{sizeLabel(item.size)}</small>}
        </div>
        <Menu label={`${item.name} 的更多操作`}>
          {item.type === "file" && (
            <>
              <button role="menuitem" onClick={() => setPreview(item)}>
                <Eye size={15} />
                预览
              </button>
              <a role="menuitem" href={item.contentUrl} download={item.name}>
                <Download size={15} />
                下载
              </a>
            </>
          )}
          {item.type === "folder" && (
            <button role="menuitem" onClick={() => openFolder(item)}>
              <Folder size={15} />
              打开文件夹
            </button>
          )}
          {writable && !picker && (
            <>
              {item.canRename && adapter.rename && (
                <button
                  role="menuitem"
                  disabled={busy}
                  onClick={() => startEdit("rename", item)}
                >
                  <Pencil size={15} />
                  重命名
                </button>
              )}
              {item.canMove && adapter.move && (
                <button
                  role="menuitem"
                  disabled={busy}
                  onClick={() => startEdit("move", item)}
                >
                  <FolderInput size={15} />
                  移动到…
                </button>
              )}
              {item.canRemove && adapter.remove && (
                <button
                  role="menuitem"
                  className="danger"
                  disabled={busy}
                  onClick={() => startEdit("remove", item)}
                >
                  <Trash2 size={15} />
                  {item.attachment
                    ? "从项目移除"
                    : item.type === "folder"
                      ? "删除空文件夹"
                      : item.source === "core"
                        ? "从文件管理移除"
                        : "移除文件"}
                </button>
              )}
            </>
          )}
        </Menu>
      </article>
    );
  }
  const excludedFolders = (item: FileBrowserItem) => {
    if (!editor?.item || item.source !== editor.item.source) return true;
    const seen = new Set<string>();
    let current: FileBrowserItem | undefined = item;
    while (current) {
      if (current.id === editor.item.id || seen.has(current.id)) return true;
      seen.add(current.id);
      current = moveFolders.find((folder) => folder.id === current!.parentId);
    }
    return false;
  };
  return (
    <section
      className={`file-management-page shared-file-browser${picker ? " browser-picker" : ""}${compact ? " browser-compact" : ""}`}
      aria-label={picker ? "选择文件素材" : "文件管理"}
    >
      <style>{foundation + components + library + styles}</style>
      <section className={`file-browser${compact ? " is-compact" : ""}`}>
        <div className="file-browser-controls">{toolbar}</div>
        <input
          ref={input}
          type="file"
          hidden
          multiple
          aria-label="选择要上传的文件"
          onChange={(event) => {
            const files = Array.from(event.target.files || []);
            event.target.value = "";
            if (files.length)
              void run(
                (signal) =>
                  adapter.upload(files, location, signal, setProgress),
                `已上传 ${files.length} 个文件。`,
              );
          }}
        />
        <input
          ref={zip}
          type="file"
          hidden
          accept=".zip"
          aria-label="选择 ZIP 归档"
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file)
              void run(
                (signal) => adapter.importArchive!(file, location, signal),
                "归档已导入。",
              );
          }}
        />
        <input
          ref={directory}
          type="file"
          hidden
          multiple
          {...{ webkitdirectory: "" }}
          aria-label="选择要导入的文件夹"
          onChange={(event) => {
            const files = Array.from(event.target.files || []);
            event.target.value = "";
            if (files.length)
              void run(
                (signal) => adapter.importDirectory!(files, location, signal),
                "文件夹已导入。",
              );
          }}
        />
        {Boolean(
          error || loadError || projectError || data.warnings?.length,
        ) && (
          <div className="browser-feedback is-error" role="alert">
            <span>
              {[error, loadError, projectError, ...(data.warnings || [])]
                .filter(Boolean)
                .join("；")}
            </span>
            <button
              onClick={() => {
                setError("");
                refresh((v) => v + 1);
              }}
              disabled={busy}
            >
              重试
            </button>
          </div>
        )}
        {notice && (
          <p className="browser-feedback" role="status">
            {notice}
          </p>
        )}
        {busy && (
          <div className="browser-feedback" role="status">
            <span>
              {progress || (picker ? "正在导入素材…" : "正在处理文件…")}
            </span>
            <button onClick={() => transfer.current?.abort()}>取消</button>
          </div>
        )}
        {loading ? (
          <div
            className="browser-skeletons"
            aria-label="正在读取文件"
            role="status"
          >
            {Array.from({ length: 12 }, (_, i) => (
              <div key={i} />
            ))}
          </div>
        ) : shown.length ? (
          <div className={`file-items ${view}`}>
            <div className="file-folder-collection">
              {shown.filter((item) => item.type === "folder").map(itemCard)}
            </div>
            <div className="file-asset-collection">
              {shown.filter((item) => item.type === "file").map(itemCard)}
            </div>
          </div>
        ) : !loadError && !projectError && !data.warnings?.length ? (
          <div className="file-empty">
            <Folder size={36} aria-hidden />
            <strong>
              {query || kind !== "all"
                ? "没有找到匹配的文件"
                : "这里还没有文件"}
            </strong>
            <p>
              {query || kind !== "all"
                ? "试试其他关键词，或清除筛选条件。"
                : picker
                  ? "可以切换到其他来源选择素材。"
                  : "上传文件，或新建文件夹整理素材。"}
            </p>
            {query || kind !== "all" ? (
              <button
                onClick={() => {
                  setQuery("");
                  setKind("all");
                }}
              >
                清除筛选
              </button>
            ) : (
              writable &&
              !picker && (
                <button onClick={() => input.current?.click()}>
                  <Upload size={15} />
                  上传文件
                </button>
              )
            )}
          </div>
        ) : null}
        {pages > 1 && (
          <nav className="browser-pagination" aria-label="文件分页">
            <button
              disabled={currentPage === 0}
              onClick={() => setPage((value) => value - 1)}
            >
              上一页
            </button>
            <span>
              {currentPage + 1} / {pages}
            </span>
            <button
              disabled={currentPage === pages - 1}
              onClick={() => setPage((value) => value + 1)}
            >
              下一页
            </button>
          </nav>
        )}
        {picker && (
          <footer className="browser-picker-footer">
            <span>
              {selected.length
                ? `已选择 ${selected.length} 项`
                : "选择要使用的文件"}
            </span>
            {close && (
              <button type="button" onClick={close}>
                取消
              </button>
            )}
            <button
              type="button"
              className="primary"
              disabled={!selectedItems.length || busy || disabled}
              onClick={choose}
            >
              {busy ? "正在导入…" : "使用所选素材"}
            </button>
          </footer>
        )}
      </section>
      {preview && (
        <Preview
          key={preview.key}
          adapter={adapter}
          item={preview}
          close={() => setPreview(undefined)}
        />
      )}
      {editor && (
        <Modal
          title={
            editor.action === "create"
              ? "新建文件夹"
              : editor.action === "rename"
                ? "重命名"
                : editor.action === "move"
                  ? "移动到文件夹"
                  : "移除文件"
          }
          close={() => {
            if (!busy) setEditor(undefined);
          }}
        >
          <form
            className="browser-editor"
            onSubmit={(event) => {
              event.preventDefault();
              if (
                (editor.action === "create" || editor.action === "rename") &&
                !editorName.trim()
              )
                return;
              void run(async () => {
                if (editor.action === "create")
                  await adapter.createFolder!(editorName.trim(), location);
                else if (editor.action === "rename")
                  await adapter.rename!(editor.item!, editorName.trim());
                else if (editor.action === "move")
                  await adapter.move!(
                    editor.item!,
                    moveFolders.find((folder) => folder.key === moveTarget) ||
                      null,
                  );
                else await adapter.remove!(editor.item!);
              }, "操作已完成。");
            }}
          >
            {editor.action === "create" || editor.action === "rename" ? (
              <label>
                名称
                <input
                  aria-label="名称"
                  autoFocus
                  required
                  maxLength={240}
                  value={editorName}
                  onChange={(event) => setEditorName(event.target.value)}
                  disabled={busy}
                />
              </label>
            ) : editor.action === "move" ? (
              <label>
                目标文件夹
                <select
                  aria-label="目标文件夹"
                  value={moveTarget}
                  onChange={(event) => setMoveTarget(event.target.value)}
                  disabled={busy}
                >
                  <option value="">根目录</option>
                  {moveFolders
                    .filter((folder) => !excludedFolders(folder))
                    .map((folder) => (
                      <option key={folder.key} value={folder.key}>
                        {folder.name}
                      </option>
                    ))}
                </select>
              </label>
            ) : (
              <>
                <p>{editor.item?.name}</p>
                <p>
                  {editor.item?.attachment
                    ? "只移除本项目中的引用，原始文件会保留。"
                    : editor.item?.type === "folder"
                      ? "只能删除空文件夹。"
                      : editor.item?.source === "core"
                        ? "从文件管理中移除，已被项目引用的原件会保留。"
                        : "将调用工坊的文件移除操作；正在使用的素材会由工坊检查保护。"}
                </p>
              </>
            )}
            {error && <p role="alert">{error}</p>}
            <footer>
              <button
                type="button"
                disabled={busy}
                onClick={() => setEditor(undefined)}
              >
                取消
              </button>
              <button type="submit" disabled={busy}>
                {busy ? "正在处理…" : "确认"}
              </button>
            </footer>
          </form>
        </Modal>
      )}
    </section>
  );
}
export function FileBrowserPicker(props: SharedFileBrowserProps) {
  return (
    <Modal
      title="选择素材"
      close={() => props.close?.()}
      className="fpl-browser-picker"
    >
      <SharedFileBrowser {...props} picker />
    </Modal>
  );
}
