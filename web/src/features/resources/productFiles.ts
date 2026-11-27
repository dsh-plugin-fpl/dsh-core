/** Product file access stays on published, session-scoped HTTP boundaries. */
export type FileRequest = (path: string, init?: RequestInit) => Promise<Response>;
export type FileProject = { id: string; title?: string; name?: string };
export type ProjectReceipt = {
  id: string; project_id: string; source: "core" | "application";
  source_id: string; resource_id?: string; name: string; content_url: string;
  mime_type?: string; byte_size?: number; media_kind?: string;
};
export type ProductFile = {
  key: string; id: string; source: "core" | "application"; name: string;
  mime_type: string; byte_size: number; media_kind: string; content_url: string;
  receipts: ProjectReceipt[];
};
export const fileRequest: FileRequest = async (path, init) => {
  const response = await fetch(path, { ...init, credentials: "include" });
  if (!response.ok) throw new Error(`无法读取文件（${response.status}），请核对账号和项目权限。`);
  return response;
};
export async function fileJson<T>(request: FileRequest, path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init);
  if (!response.ok) throw new Error(`无法处理文件（${response.status}）`);
  return response.json();
}
export async function productApi<T>(path: string, body?: unknown, method?: "POST" | "DELETE"): Promise<T> {
  // Used by embedded Core pages; the product cookie name is configured at bootstrap.
  const { coreCsrfToken } = await import("@platform/embedded");
  return fileJson(fileRequest, "/v1/app/" + path, {
    method: method || (body ? "POST" : "GET"),
    headers: body ? { "Content-Type": "application/json", "X-CSRF-Token": coreCsrfToken() } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
}
type RawFile = Partial<ProductFile> & { id: string; display_name?: string; original_filename?: string };
function normalize(row: RawFile, source: ProductFile["source"]): ProductFile {
  return {
    key: source + ":" + row.id, id: row.id, source,
    name: row.name || row.display_name || row.original_filename || row.id,
    mime_type: row.mime_type || "application/octet-stream", byte_size: row.byte_size || 0,
    media_kind: row.media_kind || "file", receipts: [],
    content_url: source === "core"
      ? `/v1/core/file-library/entries/${encodeURIComponent(row.id)}/content`
      : `/v1/files/${encodeURIComponent(row.id)}/download`,
  };
}
export async function loadProductFiles(request: FileRequest, project = "", signal?: AbortSignal) {
  const read = <T,>(path: string) => fileJson<T>(request, path, { signal });
  async function coreFiles() {
    const folders = await read<{ id: string }[]>("/v1/core/file-library/folders");
    const items: RawFile[] = [];
    for (const folder of [null, ...folders.map(x => x.id)]) {
      let cursor: string | undefined;
      do {
        const params = new URLSearchParams({ limit: "200" });
        if (folder) params.set("folder_id", folder);
        if (cursor) params.set("after", cursor);
        const page = await read<{ items: RawFile[]; next_cursor?: string }>("/v1/core/file-library?" + params);
        items.push(...page.items); cursor = page.next_cursor;
      } while (cursor);
    }
    return items;
  }
  // A project read must succeed before rendering any of that project's attachments.
  const receipts = project
    ? (await read<{ items: ProjectReceipt[] }>("/v1/app/project-files?project_id=" + encodeURIComponent(project))).items
    : null;
  const results = await Promise.allSettled([
    project ? Promise.resolve([] as RawFile[]) : coreFiles(),
    project ? Promise.resolve({ items: [] as RawFile[] }) : read<{ items: RawFile[] }>("/v1/app/legacy-application-files"),
    receipts ? Promise.resolve({ items: receipts }) : read<{ items: ProjectReceipt[] }>("/v1/app/legacy-file-receipts"),
    read<{ items: FileProject[] }>("/v1/app/file-projects"),
  ]);
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  const files = new Map<string, ProductFile>();
  const warnings: string[] = [];
  const labels = ["文件库", "工坊素材", "项目附件", "项目列表"];
  results.forEach((result, i) => { if (result.status === "rejected") warnings.push(`${labels[i]}读取失败：${String(result.reason)}`); });
  const [core, application, archived, projects] = results;
  if (core.status === "fulfilled") for (const row of core.value) { const f = normalize(row, "core"); files.set(f.key, f); }
  if (application.status === "fulfilled") for (const row of application.value.items) { const f = normalize(row, "application"); files.set(f.key, f); }
  const available = projects.status === "fulfilled" ? projects.value.items : [];
  if (archived.status === "fulfilled") for (const receipt of archived.value.items) {
    if (!project && !available.some(p => p.id === receipt.project_id)) continue;
    const id = receipt.resource_id || receipt.source_id;
    const key = "core:" + id;
    const item = files.get(key) || normalize({ ...receipt, id }, "core");
    // Archived copies may be hidden from the ordinary Core library. Always read
    // through the project-authorized content URL when only a receipt exposes it.
    if (!files.has(key)) item.content_url = receipt.content_url;
    item.receipts.push(receipt);
    files.set(key, item);
  }
  return { items: [...files.values()], projects: available, warnings };
}
export function acceptsFile(file: ProductFile, accept = "") {
  if (!accept) return true;
  return accept.split(",").some(value => {
    const token = value.trim().toLowerCase();
    return token.startsWith(".") ? file.name.toLowerCase().endsWith(token)
      : token.endsWith("/*") ? file.mime_type.startsWith(token.slice(0, -1))
      : file.mime_type === token;
  });
}
export async function readProductFile(request: FileRequest, item: ProductFile, signal?: AbortSignal) {
  const response = await request(item.content_url, { signal });
  if (!response.ok) throw new Error(`原件读取失败（${response.status}）`);
  return new File([await response.blob()], item.name, { type: item.mime_type });
}
