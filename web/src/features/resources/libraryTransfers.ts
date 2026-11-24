import { fetchApiJson, requestApi } from "@platform/api/client";
type ImportResult = { files: number; folders: number };
function resultLabel(result: ImportResult) {
  return `已导入 ${result.folders} 个文件夹、${result.files} 个文件`;
}
async function importFiles(
  path: string,
  form: FormData,
  parent: string | null,
  signature: string,
) {
  const key = `core-library-import:${parent || "root"}:${signature}`;
  let requestId: string = crypto.randomUUID();
  try {
    requestId = sessionStorage.getItem(key) || requestId;
    sessionStorage.setItem(key, requestId);
  } catch {}
  form.set("request_id", requestId);
  if (parent) form.set("parent_id", parent);
  try {
    const result = await fetchApiJson<ImportResult>(path, {
      method: "POST",
      body: form,
    });
    try {
      sessionStorage.removeItem(key);
    } catch {}
    return resultLabel(result);
  } catch (error) {
    const receipt = await fetchApiJson<{
      status: string;
      result?: ImportResult;
    }>(`/v1/file-library/imports/${requestId}`).catch(() => null);
    if (receipt?.result) {
      try {
        sessionStorage.removeItem(key);
      } catch {}
      return resultLabel(receipt.result);
    }
    if (receipt)
      throw new Error(
        "导入尚未完成，已保存回执。再次选择同一批文件可继续，已完成部分不会重复导入。",
      );
    throw error;
  }
}
export function importArchive(file: File, parent: string | null) {
  const form = new FormData();
  form.set("archive", file);
  return importFiles(
    "/v1/file-library/archive-imports",
    form,
    parent,
    `zip:${file.name}:${file.size}:${file.lastModified}`,
  );
}
export function importDirectory(files: File[], parent: string | null) {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
    form.append("paths", file.webkitRelativePath || file.name);
  }
  return importFiles(
    "/v1/file-library/directory-imports",
    form,
    parent,
    JSON.stringify(
      files.map((file) => [
        file.webkitRelativePath || file.name,
        file.size,
        file.lastModified,
      ]),
    ),
  );
}
export async function exportArchive(folder: string | null) {
  const response = await requestApi(
    "/v1/file-library/folder-export" +
      (folder ? "?folder_id=" + encodeURIComponent(folder) : ""),
  );
  const url = URL.createObjectURL(await response.blob()),
    link = document.createElement("a");
  link.href = url;
  link.download = "files.zip";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
  return "文件夹归档已下载";
}
