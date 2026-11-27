import type {
  FileBrowserAdapter,
  FileBrowserItem,
  FileBrowserLocation,
  FileBrowserPage,
  FileBrowserProject,
  FileRequest,
  NativeProjectFiles,
} from "./fileBrowserTypes";

type Row = Record<string, unknown>;
const text = (value: unknown) => (typeof value === "string" ? value : "");
const number = (value: unknown) =>
  typeof value === "number" ? value : undefined;
export function browserFile(
  row: Row,
  source: FileBrowserItem["source"],
  projectId?: string,
): FileBrowserItem {
  const id = text(row.id),
    mime = text(row.mime_type || row.mimeType) || "application/octet-stream";
  const kind = mime.startsWith("image/")
    ? "image"
    : mime.startsWith("audio/")
      ? "audio"
      : mime.startsWith("video/")
        ? "video"
        : mime === "application/pdf"
          ? "pdf"
          : mime.startsWith("text/") ||
              ["application/json", "application/xml"].includes(mime)
            ? "text"
            : "file";
  return {
    key: `${source}:${projectId || ""}:${id}`,
    id,
    source,
    projectId,
    type: "file",
    name:
      text(
        row.name ||
          row.display_name ||
          row.displayName ||
          row.original_filename ||
          row.originalFilename,
      ) || id,
    mimeType: mime,
    kind,
    size: number(row.byte_size ?? row.byteSize) ?? 0,
    revision: number(row.revision),
    updatedAt:
      text(
        row.updated_at || row.updatedAt || row.created_at || row.createdAt,
      ) || null,
    parentId: text(row.folder_id) || null,
    contentUrl:
      text(row.content_url || row.contentUrl) ||
      (source === "core"
        ? `/v1/core/file-library/entries/${encodeURIComponent(id)}/content`
        : `/v1/files/${encodeURIComponent(id)}/download`),
    thumbnailUrl:
      text(row.thumbnail_url || row.thumbnailUrl) ||
      (source !== "core" && ["image", "video"].includes(kind)
        ? `/v1/files/${encodeURIComponent(id)}/thumbnail?size=320`
        : undefined),
    canRename: number(row.revision) !== undefined,
    canMove: source !== "project" && number(row.revision) !== undefined,
    canRemove: number(row.revision) !== undefined,
  };
}
function browserFolder(
  row: Row,
  source: "core" | "application",
  projectId?: string,
): FileBrowserItem {
  return {
    ...browserFile(row, source, projectId),
    type: "folder",
    parentId: text(row.parent_id) || null,
    canRename: true,
    canMove: true,
    canRemove: true,
  };
}
export async function browserJson<T>(
  request: FileRequest,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await request(path, init);
  if (!response.ok) {
    let detail = "";
    try {
      const data = await response.json();
      detail =
        typeof data.detail === "string" ? data.detail : data.message || "";
    } catch {
      /* The status remains useful for non-JSON errors. */
    }
    throw new Error(
      response.status === 409
        ? "文件已被修改，请刷新后重试。"
        : `文件操作失败（${response.status}）${detail ? `：${detail}` : ""}`,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}
export function createFileBrowserAdapter(options: {
  id: string;
  request: FileRequest;
  projects: (signal?: AbortSignal) => Promise<FileBrowserProject[]>;
  projectFiles: NativeProjectFiles;
}): FileBrowserAdapter {
  const { request, projectFiles } = options;
  const json = <T>(path: string, init?: RequestInit) =>
    browserJson<T>(request, path, init);
  const write = (
    path: string,
    method: string,
    body: unknown,
    signal?: AbortSignal,
  ) =>
    json(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  const sourcePath = (source: FileBrowserItem["source"]) =>
    source === "core" ? "/v1/core/file-library" : "/v1";
  async function folders(
    location: FileBrowserLocation,
    signal?: AbortSignal,
  ): Promise<FileBrowserItem[]> {
    if (location.projectId)
      return (
        await projectFiles.browse(location.projectId, signal)
      ).items.filter((x) => x.type === "folder");
    const sources = location.source
      ? [location.source]
      : (["core", "application"] as const);
    const result = await Promise.all(
      sources.map(async (source) => {
        const response = await json<Row[] | { items: Row[] }>(
          source === "core"
            ? "/v1/core/file-library/folders"
            : "/v1/file-folders",
          { signal },
        );
        return (Array.isArray(response) ? response : response.items).map(
          (row) => browserFolder(row, source),
        );
      }),
    );
    return result.flat();
  }
  async function corePage(location: FileBrowserLocation, signal?: AbortSignal) {
    const params = new URLSearchParams({ limit: "200" });
    if (location.folderId) params.set("folder_id", location.folderId);
    const files: FileBrowserItem[] = [],
      seen = new Set<string>();
    let value: {
      items: Row[];
      folders: Row[];
      breadcrumbs: Row[];
      next_cursor?: string;
    };
    do {
      value = await json("/v1/core/file-library?" + params, { signal });
      files.push(...value.items.map((row) => browserFile(row, "core")));
      if (value.next_cursor) {
        if (seen.has(value.next_cursor))
          throw Error("文件分页异常，请刷新后重试。");
        seen.add(value.next_cursor);
        params.set("after", value.next_cursor);
      }
    } while (value.next_cursor);
    return {
      items: [
        ...value.folders.map((row) => browserFolder(row, "core")),
        ...files,
      ],
      breadcrumbs: value.breadcrumbs.map((row) => browserFolder(row, "core")),
    };
  }
  async function appPage(
    location: FileBrowserLocation,
    signal?: AbortSignal,
  ): Promise<FileBrowserPage> {
    const params = new URLSearchParams({ include_svg: "true" });
    if (location.folderId) params.set("folder_id", location.folderId);
    const [result, all] = await Promise.all([
      json<{ items: Row[] }>("/v1/files?" + params, { signal }),
      folders({ source: "application" }, signal),
    ]);
    const breadcrumbs: FileBrowserItem[] = [],
      seen = new Set<string>();
    let current = all.find((f) => f.id === location.folderId);
    while (current && !seen.has(current.id)) {
      seen.add(current.id);
      breadcrumbs.unshift(current);
      current = all.find((f) => f.id === current!.parentId);
    }
    return {
      items: [
        ...all.filter((f) => (f.parentId || undefined) === location.folderId),
        ...result.items.map((row) => browserFile(row, "application")),
      ],
      breadcrumbs,
    };
  }
  async function attachments(
    projectId: string,
    signal?: AbortSignal,
  ): Promise<FileBrowserItem[]> {
    const result = await json<{ items: Row[] }>(
      "/v1/app/project-files?" + new URLSearchParams({ project_id: projectId }),
      { signal },
    );
    return result.items.map((row) => ({
      ...browserFile(
        { ...row, id: row.resource_id || row.source_id },
        "core",
        projectId,
      ),
      canRename: false,
      canMove: false,
      canRemove: true,
      attachment: {
        projectId,
        source: row.source === "application" ? "application" : "core",
        sourceId: String(row.source_id),
      },
    }));
  }
  async function browse(
    location: FileBrowserLocation,
    signal?: AbortSignal,
  ): Promise<FileBrowserPage> {
    if (location.projectId) {
      const native = await projectFiles.browse(location.projectId, signal);
      const extra = await attachments(location.projectId, signal);
      const unique = new Map(
        [...native.items, ...extra].map((item) => [item.key, item]),
      );
      const items = [...unique.values()];
      if (location.folderId) {
        const breadcrumbs: FileBrowserItem[] = [],
          seen = new Set<string>();
        let current = items.find(
          (item) => item.type === "folder" && item.id === location.folderId,
        );
        while (current && !seen.has(current.id)) {
          seen.add(current.id);
          breadcrumbs.unshift(current);
          current = items.find(
            (item) => item.type === "folder" && item.id === current!.parentId,
          );
        }
        return {
          items: items.filter((i) => i.parentId === location.folderId),
          breadcrumbs,
        };
      }
      return { ...native, items: items.filter((i) => !i.parentId) };
    }
    const sources = location.source
      ? [location.source]
      : (["application", "core"] as const);
    const results = await Promise.allSettled(
      sources.map((s) =>
        s === "core" ? corePage(location, signal) : appPage(location, signal),
      ),
    );
    signal?.throwIfAborted();
    const warnings: string[] = [],
      pages: FileBrowserPage[] = [];
    results.forEach((result, index) => {
      if (result.status === "fulfilled") pages.push(result.value);
      else
        warnings.push(
          `${sources[index] === "core" ? "文件库" : "工坊文件"}读取失败：${String(result.reason)}`,
        );
    });
    if (!pages.length) throw Error(warnings.join("；"));
    return {
      items: pages.flatMap((p) => p.items),
      breadcrumbs: pages[0].breadcrumbs,
      warnings,
    };
  }
  async function upload(
    files: File[],
    location: FileBrowserLocation,
    signal: AbortSignal,
    progress: (value: string) => void,
  ) {
    const errors: string[] = [];
    for (let i = 0; i < files.length; i++) {
      signal.throwIfAborted();
      const file = files[i];
      progress(`正在上传 ${i + 1}/${files.length}：${file.name}`);
      try {
        if (location.projectId && projectFiles.upload)
          await projectFiles.upload(location.projectId, file, signal);
        else if (location.source === "core") {
          const begin = (await write(
            "/v1/core/rpc",
            "POST",
            {
              method: "resource.upload.begin",
              params: {
                name: file.name,
                mime_type: file.type || "application/octet-stream",
                byte_size: file.size,
              },
            },
            signal,
          )) as { id: string };
          let complete = false;
          try {
            for (let offset = 0; offset < file.size; offset += 1024 * 1024) {
              signal.throwIfAborted();
              const bytes = new Uint8Array(
                await file.slice(offset, offset + 1024 * 1024).arrayBuffer(),
              );
              let binary = "";
              for (const byte of bytes) binary += String.fromCharCode(byte);
              await write(
                "/v1/core/rpc",
                "POST",
                {
                  method: "resource.upload.append",
                  params: { id: begin.id, offset, data_base64: btoa(binary) },
                },
                signal,
              );
            }
            const resource = (await write(
              "/v1/core/rpc",
              "POST",
              { method: "resource.upload.finish", params: { id: begin.id } },
              signal,
            )) as Row;
            complete = true;
            if (location.folderId) {
              const entry = (await corePage({}, signal)).items.find(
                (item) => item.id === resource.id,
              );
              if (!entry || entry.revision === undefined)
                throw Error(
                  "文件已上传到根目录，无法确认其版本，请刷新后移动。",
                );
              await write(
                `/v1/core/file-library/entries/${resource.id}`,
                "PATCH",
                {
                  expected_revision: entry.revision,
                  folder_id: location.folderId,
                },
                signal,
              );
            }
          } finally {
            if (!complete)
              await write("/v1/core/rpc", "POST", {
                method: "resource.upload.abort",
                params: { id: begin.id },
              }).catch(() => {});
          }
        } else {
          const body = new FormData();
          body.set("upload", file);
          if (location.folderId) body.set("folder_id", location.folderId);
          const result = await json<Row>("/v1/files", {
            method: "POST",
            body,
            signal,
          });
          if (location.projectId)
            await write(
              "/v1/app/project-files",
              "POST",
              {
                project_id: location.projectId,
                source: "application",
                source_id: result.id,
              },
              signal,
            );
        }
      } catch (error) {
        signal.throwIfAborted();
        errors.push(`${file.name}：${String(error)}`);
      }
    }
    if (errors.length) throw Error(errors.join("；"));
  }
  const itemPath = (item: FileBrowserItem) =>
    sourcePath(item.source) +
    (item.source === "core"
      ? `/${item.type === "folder" ? "folders" : "entries"}/`
      : `/${item.type === "folder" ? "file-folders" : "files"}/`) +
    encodeURIComponent(item.id);
  async function coreImport(
    path: string,
    body: FormData,
    location: FileBrowserLocation,
    signature: string,
    signal: AbortSignal,
  ) {
    const key = `${options.id}:core-file-import:${location.folderId || "root"}:${signature}`;
    let requestId: string = crypto.randomUUID();
    try {
      requestId = sessionStorage.getItem(key) || requestId;
      sessionStorage.setItem(key, requestId);
    } catch {
      /* Import receipts remain server-owned. */
    }
    body.set("request_id", requestId);
    if (location.folderId) body.set("parent_id", location.folderId);
    const complete = () => {
      try {
        sessionStorage.removeItem(key);
      } catch {
        /* Storage may be unavailable. */
      }
    };
    try {
      await json(path, { method: "POST", body, signal });
      complete();
    } catch (error) {
      signal.throwIfAborted();
      const receipt = await json<{ result?: unknown }>(
        `/v1/core/file-library/imports/${requestId}`,
        { signal },
      ).catch(() => null);
      if (receipt?.result) {
        complete();
        return;
      }
      if (receipt)
        throw Error(
          "导入尚未完成，已保存回执。再次选择同一批文件可继续，已完成部分不会重复导入。",
        );
      throw error;
    }
  }
  const adapter: FileBrowserAdapter = {
    id: options.id,
    request,
    projects: options.projects,
    browse,
    upload,
    folders,
    async createFolder(name, location) {
      if (location.projectId) throw Error("请在项目素材面板管理目录。");
      await write(
        location.source === "core"
          ? "/v1/core/file-library/folders"
          : "/v1/file-folders",
        "POST",
        {
          name,
          parent_id: location.folderId || null,
          ...(location.source !== "core" ? { scope: "library" } : {}),
        },
      );
    },
    async rename(item, name) {
      if (item.source === "project") {
        if (!projectFiles.rename) throw Error("此素材不支持重命名。");
        await projectFiles.rename(item, name);
        return;
      }
      await write(itemPath(item), "PATCH", {
        expected_revision: item.revision,
        [item.type === "folder" || item.source === "core"
          ? "name"
          : "display_name"]: name,
      });
    },
    async move(item, folder) {
      if (
        item.source === "project" ||
        (folder && item.source !== folder.source)
      )
        throw Error("只能在同一来源中移动文件。");
      await write(itemPath(item), "PATCH", {
        expected_revision: item.revision,
        [item.type === "folder" ? "parent_id" : "folder_id"]:
          folder?.id || null,
        ...(item.source === "application" ? { move_to_root: !folder } : {}),
      });
    },
    async remove(item) {
      if (item.attachment) {
        await write("/v1/app/project-files", "DELETE", {
          project_id: item.attachment.projectId,
          source: item.attachment.source,
          source_id: item.attachment.sourceId,
        });
        return;
      }
      if (item.source === "project") {
        if (!projectFiles.remove) throw Error("请在项目内解除素材引用。");
        await projectFiles.remove(item);
        return;
      }
      await json(itemPath(item) + "?expected_revision=" + item.revision, {
        method: "DELETE",
      });
    },
    async importArchive(file, location, signal) {
      const body = new FormData();
      body.set("archive", file);
      if (location.source === "core") {
        await coreImport(
          "/v1/core/file-library/archive-imports",
          body,
          location,
          `zip:${file.name}:${file.size}:${file.lastModified}`,
          signal,
        );
        return;
      }
      if (location.folderId) body.set("parent_folder_id", location.folderId);
      await json("/v1/file-library/folder-archive-imports", {
        method: "POST",
        body,
        signal,
      });
    },
    async importDirectory(files, location, signal) {
      if (location.source === "core") {
        const body = new FormData();
        for (const file of files) {
          body.append("files", file);
          body.append("paths", file.webkitRelativePath || file.name);
        }
        await coreImport(
          "/v1/core/file-library/directory-imports",
          body,
          location,
          JSON.stringify(
            files.map((file) => [
              file.webkitRelativePath || file.name,
              file.size,
              file.lastModified,
            ]),
          ),
          signal,
        );
      } else {
        const body = new FormData();
        for (const file of files) {
          body.append("files", file);
          body.append("relative_paths", file.webkitRelativePath || file.name);
        }
        if (location.folderId) body.set("parent_folder_id", location.folderId);
        await json("/v1/file-library/folder-entry-imports", {
          method: "POST",
          body,
          signal,
        });
      }
    },
    async exportFolder(location, signal) {
      const params = new URLSearchParams();
      if (location.folderId) params.set("folder_id", location.folderId);
      const response = await request(
        (location.source === "core"
          ? "/v1/core/file-library/folder-export"
          : "/v1/file-library/folder-exports") +
          "?" +
          params,
        { signal },
      );
      if (!response.ok) throw Error(`导出失败（${response.status}）`);
      return response.blob();
    },
  };
  return adapter;
}

/** Follow only declared document asset references, never other application stores. */
export function documentAssetIds(document: unknown): Set<string> {
  const ids = new Set<string>();
  const visit = (value: unknown) => {
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
      if (
        ["asset_id", "assetId", "file_id", "fileId"].includes(key) &&
        typeof child === "string"
      )
        ids.add(child);
      else if (["src", "href"].includes(key) && typeof child === "string") {
        const match = child.match(
          /^\/v1\/files\/([^/]+)\/(?:content|download)(?:\?|$)/,
        );
        if (match) ids.add(match[1]);
      } else if (typeof child === "object") visit(child);
    }
  };
  visit(document);
  return ids;
}

export async function referencedBrowserFiles(
  request: FileRequest,
  documents: unknown[],
  projectId: string,
  signal?: AbortSignal,
): Promise<FileBrowserItem[]> {
  const ids = new Set(
    documents.flatMap((document) => [...documentAssetIds(document)]),
  );
  const items: FileBrowserItem[] = [];
  // Bounded concurrency prevents a large notebook from opening hundreds of reads.
  const pending = [...ids];
  const workers = Array.from(
    { length: Math.min(4, pending.length) },
    async () => {
      while (pending.length) {
        signal?.throwIfAborted();
        const id = pending.shift()!;
        const response = await request(
          `/v1/files/${encodeURIComponent(id)}/content`,
          { signal, headers: { Range: "bytes=0-0" } },
        );
        if (!response.ok) throw Error(`项目素材读取失败（${response.status}）`);
        const disposition = response.headers.get("content-disposition") || "";
        const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
        let name = disposition.match(/filename="([^"]+)"/i)?.[1] || id;
        if (encoded) {
          try {
            name = decodeURIComponent(encoded);
          } catch {
            /* Keep the literal header fallback. */
          }
        }
        const length = Number(
          response.headers.get("content-range")?.split("/")[1] ||
            response.headers.get("content-length") ||
            0,
        );
        const mime =
          response.headers.get("content-type")?.split(";")[0] ||
          "application/octet-stream";
        await response.body?.cancel();
        items.push({
          ...browserFile(
            { id, name, mime_type: mime, byte_size: length },
            "project",
            projectId,
          ),
          canRename: false,
          canMove: false,
          canRemove: false,
        });
      }
    },
  );
  await Promise.all(workers);
  return items;
}
