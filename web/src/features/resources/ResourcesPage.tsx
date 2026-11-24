import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FolderPlus,
  Grid2X2,
  List,
  Search,
  Upload,
  RefreshCw,
  Download,
  FolderInput,
  FileArchive,
} from "lucide-react";
import { fetchApiJson } from "@platform/api/client";
import { Feedback } from "@shared/ui/RequestFeedback";
import { CustomSelect } from "@shared/ui/CustomSelect";
import { FileItems, FileMenu } from "./FileItems";
import { FilePreview } from "./FilePreview";
import { FileEditor } from "./FileEditor";
import {
  libraryKey,
  kindOptions,
  sortOptions,
  uploadFile,
  type Editor,
  type LibraryEntry,
  type LibraryPage,
} from "./fileLibrary";
import {
  importArchive,
  importDirectory,
  exportArchive,
} from "./libraryTransfers";
import "./styles/asset-browser-foundation.css";
import "./styles/asset-browser-components.css";
import "./styles/file-library.css";
import "./resources.css";

export function Files() {
  const input = useRef<HTMLInputElement>(null),
    zipInput = useRef<HTMLInputElement>(null),
    directoryInput = useRef<HTMLInputElement>(null);
  const [folder, setFolder] = useState<string | null>(null),
    [cursor, setCursor] = useState<string | null>(null),
    [previous, setPrevious] = useState<(string | null)[]>([]),
    [search, setSearch] = useState(""),
    [kind, setKind] = useState("all"),
    [sort, setSort] = useState("updated");
  const [view, updateView] = useState(() => {
    try {
      return localStorage.getItem("dsh_core_file_view") === "grid"
        ? "grid"
        : "list";
    } catch {
      return "list";
    }
  });
  const [preview, setPreview] = useState<LibraryEntry | null>(null),
    [editor, setEditor] = useState<Editor | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [progress, setProgress] = useState("");
  const cache = useQueryClient();
  const q = useQuery({
    queryKey: [...libraryKey, folder, cursor, search, kind, sort],
    queryFn: ({ signal }) =>
      fetchApiJson<LibraryPage>(
        "/v1/file-library?" +
          new URLSearchParams({
            ...(folder ? { folder_id: folder } : {}),
            ...(cursor ? { after: cursor } : {}),
            ...(search ? { search } : {}),
            kind,
            sort,
          }),
        { signal },
      ),
    refetchInterval: 15000,
  });
  const resetPage = () => {
    setCursor(null);
    setPrevious([]);
  };
  const openFolder = (id: string | null) => {
    setFolder(id);
    resetPage();
    setSearch("");
    setKind("all");
  };
  const setView = (value: string) => {
    updateView(value);
    try {
      localStorage.setItem("dsh_core_file_view", value);
    } catch {}
  };
  const refresh = async () => {
    await cache.invalidateQueries({ queryKey: libraryKey });
  };
  const upload = async (files: FileList | null) => {
    if (!files?.length || busy) return;
    const selected = Array.from(files),
      target = folder;
    setBusy(true);
    setError("");
    const failures: string[] = [];
    try {
      for (let i = 0; i < selected.length; i++) {
        setProgress(
          `正在上传 ${i + 1}/${selected.length}：${selected[i].name}`,
        );
        try {
          await uploadFile(selected[i], target);
        } catch (e) {
          failures.push(`${selected[i].name}：${(e as Error).message}`);
        }
      }
      await refresh();
      setError(failures.join("；"));
    } finally {
      setBusy(false);
      setProgress("");
    }
  };
  const transfer = async (action: () => Promise<string>) => {
    if (busy) return;
    setBusy(true);
    setError("");
    setProgress("正在传输文件，请保持页面打开…");
    try {
      setProgress(await action());
      await refresh();
    } catch (e) {
      setError((e as Error).message);
      setProgress("");
    } finally {
      setBusy(false);
    }
  };
  const data = q.data;
  return (
    <section className="file-management-page core-file-management">
      <section className="file-browser" aria-label="文件管理">
        <div className="file-browser-controls">
          <div className="file-browser-toolbar">
            <div className="file-location">
              <nav className="file-breadcrumb" aria-label="文件夹路径">
                <button
                  type="button"
                  aria-label="全部文件"
                  onClick={() => openFolder(null)}
                >
                  <span role="heading" aria-level={1}>
                    文件管理
                  </span>
                </button>
                {data?.breadcrumbs.map((item) => (
                  <span key={item.id}>
                    <i aria-hidden>/</i>
                    <button type="button" onClick={() => openFolder(item.id)}>
                      {item.name}
                    </button>
                  </span>
                ))}
              </nav>
              {data && (
                <small>
                  {data.folder_count} 个文件夹 · {data.file_count} 个文件
                </small>
              )}
            </div>
            <div className="file-primary-actions">
              <button
                type="button"
                className="primary"
                disabled={busy}
                onClick={() => input.current?.click()}
              >
                <Upload aria-hidden />
                <span>{busy ? "传输中…" : "上传文件"}</span>
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => setEditor({ action: "create" })}
              >
                <FolderPlus aria-hidden />
                新建文件夹
              </button>
              <FileMenu label="更多操作">
                <button
                  role="menuitem"
                  disabled={busy}
                  onClick={() => void transfer(() => exportArchive(folder))}
                >
                  <Download aria-hidden />
                  导出当前文件夹 ZIP
                </button>
                <button
                  role="menuitem"
                  disabled={busy}
                  onClick={() => zipInput.current?.click()}
                >
                  <FileArchive aria-hidden />
                  导入 ZIP 归档
                </button>
                <button
                  role="menuitem"
                  disabled={busy}
                  onClick={() => directoryInput.current?.click()}
                >
                  <FolderInput aria-hidden />
                  导入本地文件夹
                </button>
                <button role="menuitem" onClick={() => void refresh()}>
                  <RefreshCw aria-hidden />
                  刷新
                </button>
              </FileMenu>
            </div>
          </div>
          <div className="file-browser-filters">
            <label>
              <Search aria-hidden />
              <input
                aria-label="搜索全部文件"
                placeholder="搜索文件或文件夹"
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  resetPage();
                }}
              />
            </label>
            <CustomSelect
              ariaLabel="文件类型"
              className="file-filter-select"
              value={kind}
              options={kindOptions}
              onChange={(value) => {
                setKind(value);
                resetPage();
              }}
            />
            <CustomSelect
              ariaLabel="文件排序"
              className="file-filter-select"
              value={sort}
              options={sortOptions}
              onChange={(value) => {
                setSort(value);
                resetPage();
              }}
            />
            <div
              className="file-view-toggle"
              role="group"
              aria-label="文件视图"
            >
              <button
                type="button"
                aria-label="网格视图"
                aria-pressed={view === "grid"}
                className={view === "grid" ? "active" : ""}
                onClick={() => setView("grid")}
              >
                <Grid2X2 aria-hidden />
              </button>
              <button
                type="button"
                aria-label="列表视图"
                aria-pressed={view === "list"}
                className={view === "list" ? "active" : ""}
                onClick={() => setView("list")}
              >
                <List aria-hidden />
              </button>
            </div>
          </div>
        </div>
        <input
          ref={input}
          aria-label="选择上传文件"
          type="file"
          multiple
          hidden
          disabled={busy}
          onChange={(event) => {
            void upload(event.target.files);
            event.target.value = "";
          }}
        />
        <input
          ref={zipInput}
          aria-label="选择 ZIP 归档"
          type="file"
          accept=".zip"
          hidden
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void transfer(() => importArchive(file, folder));
            event.target.value = "";
          }}
        />
        <input
          ref={directoryInput}
          aria-label="选择本地文件夹"
          type="file"
          multiple
          hidden
          disabled={busy}
          {...{ webkitdirectory: "", directory: "" }}
          onChange={(event) => {
            const files = Array.from(event.target.files || []);
            if (files.length)
              void transfer(() => importDirectory(files, folder));
            event.target.value = "";
          }}
        />
        <Feedback error={error || q.error?.message} />
        {progress && (
          <p className="file-notice" role="status">
            {progress}
          </p>
        )}
        {q.isPending ? (
          <p className="file-empty" role="status">
            正在读取文件…
          </p>
        ) : q.error ? (
          <div className="file-empty">
            <p>文件暂时无法读取。</p>
            <button
              onClick={() => {
                resetPage();
                void q.refetch();
              }}
            >
              重试
            </button>
          </div>
        ) : (
          data && (
            <>
              <FileItems
                folders={data.folders}
                files={data.items}
                view={view}
                openFolder={openFolder}
                preview={setPreview}
                edit={setEditor}
              />
              {!data.items.length && !data.folders.length && (
                <div className="file-empty">
                  <p>
                    {search || kind !== "all"
                      ? "没有找到匹配的文件或文件夹。"
                      : "此文件夹还没有内容，上传文件或新建文件夹后会显示在这里。"}
                  </p>
                </div>
              )}
              {(!!previous.length || data.next_cursor) && (
                <nav className="file-pagination" aria-label="文件分页">
                  <span>每页最多 100 个文件</span>
                  <button
                    className="btn"
                    disabled={!previous.length}
                    onClick={() => {
                      setCursor(previous.at(-1) ?? null);
                      setPrevious(previous.slice(0, -1));
                    }}
                  >
                    上一页
                  </button>
                  <button
                    className="btn"
                    disabled={!data.next_cursor}
                    onClick={() => {
                      setPrevious([...previous, cursor]);
                      setCursor(data.next_cursor ?? null);
                    }}
                  >
                    下一页
                  </button>
                </nav>
              )}
            </>
          )
        )}
      </section>
      {preview && (
        <FilePreview
          key={preview.id}
          file={preview}
          close={() => setPreview(null)}
        />
      )}
      {editor && (
        <FileEditor
          editor={editor}
          folderId={folder}
          close={() => setEditor(null)}
          saved={refresh}
        />
      )}
    </section>
  );
}
