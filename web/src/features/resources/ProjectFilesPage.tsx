import "./project-files.css";
import { uploadFile } from "./fileLibrary";
import { useEffect, useState, useRef } from "react";
import { coreUrl, coreProduct, coreCsrfToken } from "@platform/embedded";
import { fetchApiJson } from "@platform/api/client";

type Project = { id: string; title?: string; name?: string };
type Entry = {
  id: string;
  name?: string;
  display_name?: string;
  original_filename?: string;
  source: string;
  source_id?: string;
  project_id?: string;
  content_url?: string;
};
export async function productApi<T>(
  path: string,
  body?: unknown,
  method?: "POST" | "DELETE",
): Promise<T> {
  const token = coreCsrfToken();
  const response = await fetch("/v1/app/" + path, {
    credentials: "include",
    method: method || (body ? "POST" : "GET"),
    headers: body
      ? { "Content-Type": "application/json", "X-CSRF-Token": token }
      : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok)
    throw new Error(
      `无法处理文件（${response.status}），请核对项目权限与来源文件。`,
    );
  return response.json();
}
export function ProjectFilesPage({ legacy = false }: { legacy?: boolean }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState(
    new URLSearchParams(location.search).get("project") || "",
  );
  const [items, setItems] = useState<Entry[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const serial = useRef(0);
  async function load() {
    const version = ++serial.current;
    setLoading(true);
    setError("");
    setItems([]);
    try {
      const data = await productApi<{ items: Project[] }>("file-projects");
      if (version !== serial.current) return;
      setProjects(data.items);
      if (legacy) {
        const [core, application, receipts] = await Promise.all([
          fetchApiJson<{ items: Entry[]; next_cursor?: string }>(
            "/v1/file-library?limit=200",
          ),
          productApi<{ items: Entry[] }>("legacy-application-files"),
          productApi<{ items: Entry[] }>("legacy-file-receipts"),
        ]);
        // Follow all cursor pages, including nested folders through the search index.
        const coreItems = [...core.items];
        let cursor = core.next_cursor;
        while (cursor) {
          const page = await fetchApiJson<{
            items: Entry[];
            next_cursor?: string;
          }>("/v1/file-library?limit=200&after=" + encodeURIComponent(cursor));
          coreItems.push(...page.items);
          cursor = page.next_cursor;
        }
        const folders = await fetchApiJson<{ id: string }[]>(
          "/v1/file-library/folders",
        );
        for (const folder of folders) {
          let after: string | undefined;
          do {
            const page = await fetchApiJson<{
              items: Entry[];
              next_cursor?: string;
            }>(
              "/v1/file-library?limit=200&folder_id=" +
                encodeURIComponent(folder.id) +
                (after ? "&after=" + encodeURIComponent(after) : ""),
            );
            coreItems.push(...page.items);
            after = page.next_cursor;
          } while (after);
        }
        const valid = new Set(data.items.map((x) => x.id));
        const mapped = new Set(
          receipts.items
            .filter((x) => x.project_id && valid.has(x.project_id))
            .map((x) => x.source + ":" + x.source_id),
        );
        if (version !== serial.current) return;
        setItems(
          [
            ...coreItems.map((x) => ({
              ...x,
              source: "core",
              content_url: "/v1/core/file-library/entries/" + x.id + "/content",
            })),
            ...application.items.map((x) => ({
              ...x,
              source: "application",
              content_url: "/v1/files/" + x.id + "/download",
            })),
          ].filter((x) => !mapped.has(x.source + ":" + x.id)),
        );
      } else {
        const files = project
          ? (
              await productApi<{ items: Entry[] }>(
                "project-files?project_id=" + encodeURIComponent(project),
              )
            ).items
          : [];
        if (version === serial.current) setItems(files);
      }
    } catch (e) {
      if (version === serial.current) setError(String(e));
    } finally {
      if (version === serial.current) setLoading(false);
    }
  }
  useEffect(() => {
    void load();
    setSelected([]);
    return () => {
      serial.current++;
    };
  }, [project, legacy]);
  async function archive() {
    if (!project || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    let count = 0;
    try {
      for (const item of items.filter((x) =>
        selected.includes(x.source + ":" + x.id),
      )) {
        await productApi("project-files", {
          project_id: project,
          source: item.source,
          source_id: item.id,
        });
        count++;
      }
      setSelected([]);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      setNotice(`已归档 ${count} 个文件；原件与原引用保留。`);
    }
  }
  async function upload(files: FileList | null) {
    if (!project || !files || busy) return;
    setBusy(true);
    setError("");
    try {
      for (const file of Array.from(files)) {
        const resource = await uploadFile(file, null);
        await productApi("project-files", {
          project_id: project,
          source: "core",
          source_id: resource.id,
        });
      }
      await load();
    } catch (e) {
      setError(String(e) + "；已上传的原件可从设置中的旧文件整理继续归档。");
    } finally {
      setBusy(false);
    }
  }
  async function remove(item: Entry) {
    setBusy(true);
    setError("");
    try {
      await productApi(
        "project-files",
        { project_id: project, source: item.source, source_id: item.source_id },
        "DELETE",
      );
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="page project-files-page">
      <h1>{legacy ? "旧文件整理" : "项目文件"}</h1>
      <p>
        {legacy
          ? "将旧文件关联到对应项目。归档不会移动或删除原件；无法确认归属的文件可以保留在这里或下载导出。"
          : "项目素材与附件。编辑器已有素材入口继续管理原有业务文件。"}
      </p>
      <p>
        <a href={coreProduct()?.projectsUrl}>返回项目列表</a> ·{" "}
        <a href={coreUrl("/storage")}>设置</a>
      </p>
      <label>
        目标项目{" "}
        <select
          value={project}
          disabled={busy}
          onChange={(e) => setProject(e.target.value)}
        >
          <option value="">选择项目</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.title || p.name || p.id}
            </option>
          ))}
        </select>
      </label>
      {!loading && !projects.length && (
        <p>请先返回工坊创建项目，再整理文件。</p>
      )}
      <label>
        {" "}
        搜索文件{" "}
        <input value={query} onChange={(e) => setQuery(e.target.value)} />
      </label>
      {!legacy && (
        <label>
          上传项目附件{" "}
          <input
            type="file"
            multiple
            disabled={!project || busy}
            onChange={(e) => {
              void upload(e.target.files);
              e.target.value = "";
            }}
          />
        </label>
      )}
      {legacy && (
        <button
          disabled={!project || !selected.length || busy}
          onClick={() => void archive()}
        >
          {busy ? "正在归档…" : "归档到项目"}
        </button>
      )}
      {legacy && project && (
        <p>
          <a
            href={coreUrl(
              "/project-files?project=" + encodeURIComponent(project),
            )}
          >
            查看目标项目文件
          </a>
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <ul>
        {items
          .filter((x) =>
            (x.name || x.display_name || x.original_filename || "")
              .toLowerCase()
              .includes(query.toLowerCase()),
          )
          .map((x) => {
            const key = x.source + ":" + (x.source_id || x.id);
            return (
              <li key={key}>
                {legacy && (
                  <input
                    type="checkbox"
                    aria-label={"选择 " + (x.name || x.display_name || x.id)}
                    checked={selected.includes(key)}
                    disabled={busy}
                    onChange={(e) =>
                      setSelected((s) =>
                        e.target.checked
                          ? [...s, key]
                          : s.filter((v) => v !== key),
                      )
                    }
                  />
                )}{" "}
                <span className="project-file-name">
                  {x.name || x.display_name || x.original_filename || x.id}
                </span>
                <a href={x.content_url} target="_blank" rel="noreferrer">
                  预览 / 下载
                </a>
                {!legacy && (
                  <button disabled={busy} onClick={() => void remove(x)}>
                    移除项目引用
                  </button>
                )}
              </li>
            );
          })}
      </ul>
      {loading && <p role="status">正在读取文件…</p>}
      {!loading && !items.length && !error && (
        <p>{legacy ? "没有待整理的旧文件。" : "当前项目没有额外归档文件。"}</p>
      )}
    </section>
  );
}
