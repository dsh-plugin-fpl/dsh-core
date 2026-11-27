import { useEffect, useRef, useState } from "react";
import { FileBrowserPicker } from "./FileBrowser";
import type { FileBrowserAdapter, FileBrowserItem } from "./fileBrowserTypes";
export {
  createFileBrowserAdapter,
  browserFile,
  browserJson,
  documentAssetIds,
  referencedBrowserFiles,
} from "./fileBrowserAdapter";
export { SharedFileBrowser } from "./FileBrowser";
export type { SharedFileBrowserProps } from "./FileBrowser";
export { readBrowserFile } from "./fileBrowserTypes";
export type {
  FileBrowserAdapter,
  FileBrowserItem,
  FileBrowserLocation,
  FileBrowserPage,
  FileBrowserProject,
  FileRequest,
  FileRequest as BrowserFileRequest,
  NativeProjectFiles,
} from "./fileBrowserTypes";
import {
  acceptsFile,
  fileRequest,
  loadProductFiles,
  readProductFile,
  type FileRequest,
  type ProductFile,
} from "./productFiles";

export type ProductFilePickerProps = {
  request?: FileRequest;
  projectId?: string;
  accept?: string;
  disabled?: boolean;
  onChoose?: (file: File, signal: AbortSignal) => Promise<unknown> | void;
  label?: string;
  adapter?: FileBrowserAdapter;
  multiple?: boolean;
  onSelect?: (
    items: FileBrowserItem[],
    signal: AbortSignal,
  ) => Promise<unknown> | void;
};
/** Separate library entry: no Core configuration, identity provider or global CSS. */
export function ProductFilePicker({
  request = fileRequest,
  projectId,
  accept,
  disabled,
  onChoose,
  label = "选择素材",
  adapter,
  multiple,
  onSelect,
}: ProductFilePickerProps) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" disabled={disabled} onClick={() => setOpen(true)}>
        {label}
      </button>
      {open &&
        (adapter ? (
          <FileBrowserPicker
            adapter={adapter}
            projectId={projectId}
            accept={accept}
            disabled={disabled}
            multiple={multiple}
            onChoose={onChoose}
            onSelect={onSelect}
            close={() => setOpen(false)}
          />
        ) : (
          <Picker
            key={projectId || "library"}
            request={request}
            projectId={projectId}
            accept={accept}
            disabled={disabled}
            onChoose={onChoose}
            close={() => setOpen(false)}
          />
        ))}
    </>
  );
}
function Picker({
  request = fileRequest,
  projectId,
  accept,
  disabled,
  onChoose,
  close,
}: ProductFilePickerProps & { close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef(document.activeElement as HTMLElement | null);
  const active = useRef(true);
  const busy = useRef(false);
  const [pending, setPending] = useState(false),
    [error, setError] = useState("");
  const [items, setItems] = useState<ProductFile[]>([]),
    [query, setQuery] = useState("");
  const [project, setProject] = useState(projectId || ""),
    [loading, setLoading] = useState(true),
    [version, setVersion] = useState(0);
  const transfer = useRef<AbortController | null>(null);
  useEffect(() => {
    active.current = true;
    dialog.current?.showModal();
    return () => {
      active.current = false;
      transfer.current?.abort();
      trigger.current?.focus();
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setItems([]);
    void loadProductFiles(request, project, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setItems(result.items);
          setError(result.warnings.join("；"));
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(String(e));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [request, project, version]);
  useEffect(() => {
    if (disabled && !busy.current) close();
  }, [disabled, close]);
  async function choose(item: ProductFile) {
    if (busy.current) return;
    busy.current = true;
    setPending(true);
    setError("");
    const controller = new AbortController();
    transfer.current = controller;
    try {
      const file = await readProductFile(request, item, controller.signal);
      if (!active.current || controller.signal.aborted) return;
      await onChoose?.(file, controller.signal);
      if (active.current) close();
    } catch (e) {
      if (active.current) setError(String(e));
    } finally {
      busy.current = false;
      if (active.current) setPending(false);
    }
  }
  const visible = items.filter(
    (item) =>
      acceptsFile(item, accept) &&
      item.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );
  return (
    <dialog
      ref={dialog}
      className="fpl-file-picker"
      aria-label="选择文件库附件"
      onCancel={(event) => {
        event.preventDefault();
        if (!pending) close();
      }}
    >
      <style>{`.fpl-file-picker{color:var(--text,#252525);background:var(--surface,#fff);border:1px solid #aaa;border-radius:12px;padding:24px;width:min(720px,90vw);max-height:85vh}.fpl-file-picker::backdrop{background:#0008}.fpl-file-picker header,.fpl-file-picker nav{display:flex;align-items:center;gap:12px;margin-bottom:16px}.fpl-file-picker header strong{flex:1}.fpl-file-picker input{width:100%;box-sizing:border-box;padding:10px;margin-bottom:12px}.fpl-file-picker ul{list-style:none;padding:0;max-height:48vh;overflow:auto}.fpl-file-picker li button{width:100%;padding:12px;text-align:left;margin-bottom:6px}.fpl-file-picker button{cursor:pointer}.fpl-file-picker button:disabled{cursor:default;opacity:.5}`}</style>
      <header>
        <strong>选择文件库附件</strong>
        <button type="button" disabled={pending} onClick={close}>
          关闭
        </button>
      </header>
      {projectId && (
        <nav aria-label="附件范围">
          <button
            disabled={pending}
            aria-pressed={!!project}
            onClick={() => setProject(projectId)}
          >
            本项目附件
          </button>
          <button
            disabled={pending}
            aria-pressed={!project}
            onClick={() => setProject("")}
          >
            全部文件
          </button>
        </nav>
      )}
      <input
        autoFocus
        aria-label="搜索文件库附件"
        placeholder="搜索文件名称"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {loading && <p role="status">正在读取附件…</p>}
      {error && (
        <p role="alert">
          {error}{" "}
          <button disabled={pending} onClick={() => setVersion((v) => v + 1)}>
            重试
          </button>
        </p>
      )}
      {pending && <p role="status">正在导入素材…</p>}
      <ul>
        {visible.map((item) => (
          <li key={item.key}>
            <button
              disabled={pending}
              type="button"
              onClick={() => void choose(item)}
            >
              {item.name} · {item.mime_type}
            </button>
          </li>
        ))}
      </ul>
      {!loading && !visible.length && <p>没有匹配的附件。</p>}
    </dialog>
  );
}
