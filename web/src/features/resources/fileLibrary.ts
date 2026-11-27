import { fetchApiJson, requestApi } from "@platform/api/client";
import type { components } from "@generated/api";
export type LibraryFolder = components["schemas"]["LibraryFolder"];
export type LibraryEntry = components["schemas"]["LibraryEntry"] & { content_url?: string };
export type LibraryPage = components["schemas"]["LibraryPage"];
export type LibraryItem =
  | { type: "folder"; value: LibraryFolder }
  | { type: "file"; value: LibraryEntry };
export type Editor =
  | { action: "create" }
  | { action: "rename" | "move" | "delete"; item: LibraryItem };
export const libraryKey = ["core-file-library"];
export const kindOptions = [
  { value: "all", label: "全部类型" },
  { value: "image", label: "图片" },
  { value: "video", label: "视频" },
  { value: "audio", label: "音频" },
  { value: "pdf", label: "PDF" },
  { value: "text", label: "文本" },
  { value: "file", label: "其他文件" },
];
export const sortOptions = [
  { value: "updated", label: "最近更新" },
  { value: "name", label: "按名称" },
  { value: "size", label: "按大小" },
];
export function contentUrl(item: LibraryEntry, download = false) {
  if (item.content_url) return item.content_url;
  return coreApiUrl(`/v1/file-library/entries/${encodeURIComponent(item.id)}/content${download ? "?download=true" : ""}`);
}
export function itemPath(item: LibraryItem) {
  return `/v1/file-library/${item.type === "folder" ? "folders" : "entries"}/${encodeURIComponent(item.value.id)}`;
}
export function patchItem(item: LibraryItem, changes: object) {
  return fetchApiJson(itemPath(item), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      expected_revision: item.value.revision,
      ...changes,
    }),
  });
}
export function updatedLabel(value?: string | null) {
  if (!value || !Number.isFinite(Date.parse(value))) return "更新时间未知";
  return `${new Date(value).toLocaleDateString("zh-CN")} 更新`;
}
export function folderOptions(
  folders: LibraryFolder[],
  moving?: LibraryFolder,
) {
  const map = new Map(folders.map((folder) => [folder.id, folder]));
  return [
    { value: "", label: "文件管理（根目录）" },
    ...folders.flatMap((folder) => {
      const path: string[] = [],
        seen = new Set<string>();
      let current: LibraryFolder | undefined = folder;
      while (current) {
        if (seen.has(current.id) || current.id === moving?.id) return [];
        seen.add(current.id);
        path.unshift(current.name);
        current = current.parent_id ? map.get(current.parent_id) : undefined;
      }
      return [{ value: folder.id, label: path.join(" / ") }];
    }),
  ];
}
export async function uploadFile(file: File, folderId: string | null) {
  const rpc = <T>(method: string, params: object) =>
    fetchApiJson<T>("/v1/rpc", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ method, params }),
    });
  const begin = await rpc<{ id: string }>("resource.upload.begin", {
    name: file.name,
    mime_type: file.type || "application/octet-stream",
    byte_size: file.size,
  });
  let resource: LibraryEntry;
  try {
    for (let offset = 0; offset < file.size; offset += 1024 * 1024) {
      const bytes = new Uint8Array(
        await file.slice(offset, offset + 1024 * 1024).arrayBuffer(),
      );
      let binary = "";
      for (const byte of bytes) binary += String.fromCharCode(byte);
      await rpc("resource.upload.append", {
        id: begin.id,
        offset,
        data_base64: btoa(binary),
      });
    }
    resource = await rpc<LibraryEntry>("resource.upload.finish", {
      id: begin.id,
    });
  } catch (error) {
    await rpc("resource.upload.abort", { id: begin.id }).catch(() => {});
    throw error;
  }
  try {
    await patchItem(
      { type: "file", value: { ...resource, revision: 1 } },
      { folder_id: folderId },
    );
  } catch (error) {
    throw new Error(
      `文件已上传到根目录，归入文件夹失败：${(error as Error).message}`,
    );
  }
  return resource;
}
export async function previewText(item: LibraryEntry, signal: AbortSignal) {
  const response = await requestApi(contentUrl(item), {
    signal,
    headers: { Range: "bytes=0-204799" },
  });
  const reader = response.body?.getReader();
  if (!reader) return { text: "", truncated: false };
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (size < 204800) {
      const { value, done } = await reader.read();
      if (done) break;
      const chunk = value.subarray(0, 204800 - size);
      chunks.push(chunk);
      size += chunk.length;
    }
  } finally {
    await reader.cancel();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return {
    text: new TextDecoder().decode(bytes),
    truncated: item.byte_size > size,
  };
}
import {coreApiUrl} from '@platform/embedded';
