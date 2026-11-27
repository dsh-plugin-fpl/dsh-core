export type FileRequest = (
  path: string,
  init?: RequestInit,
) => Promise<Response>;
export type FileBrowserProject = { id: string; title: string };
export type FileBrowserLocation = {
  projectId?: string;
  folderId?: string;
  source?: "core" | "application";
};
export type FileBrowserItem = {
  key: string;
  id: string;
  name: string;
  type: "file" | "folder";
  source: "core" | "application" | "project";
  projectId?: string;
  parentId?: string | null;
  mimeType: string;
  kind: string;
  size: number;
  revision?: number;
  updatedAt?: string | null;
  contentUrl?: string;
  thumbnailUrl?: string;
  canRename?: boolean;
  canMove?: boolean;
  canRemove?: boolean;
  attachment?: {
    projectId: string;
    source: "core" | "application";
    sourceId: string;
  };
};
export type FileBrowserPage = {
  items: FileBrowserItem[];
  breadcrumbs: FileBrowserItem[];
  warnings?: string[];
};
export type FileBrowserAdapter = {
  id: string;
  request: FileRequest;
  projects: (signal?: AbortSignal) => Promise<FileBrowserProject[]>;
  browse: (
    location: FileBrowserLocation,
    signal?: AbortSignal,
  ) => Promise<FileBrowserPage>;
  upload: (
    files: File[],
    location: FileBrowserLocation,
    signal: AbortSignal,
    progress: (message: string) => void,
  ) => Promise<void>;
  createFolder?: (name: string, location: FileBrowserLocation) => Promise<void>;
  rename?: (item: FileBrowserItem, name: string) => Promise<void>;
  move?: (
    item: FileBrowserItem,
    folder: FileBrowserItem | null,
  ) => Promise<void>;
  remove?: (item: FileBrowserItem) => Promise<void>;
  folders?: (
    location: FileBrowserLocation,
    signal?: AbortSignal,
  ) => Promise<FileBrowserItem[]>;
  importArchive?: (
    file: File,
    location: FileBrowserLocation,
    signal: AbortSignal,
  ) => Promise<void>;
  importDirectory?: (
    files: File[],
    location: FileBrowserLocation,
    signal: AbortSignal,
  ) => Promise<void>;
  exportFolder?: (
    location: FileBrowserLocation,
    signal: AbortSignal,
  ) => Promise<Blob>;
};
export type NativeProjectFiles = {
  browse: (projectId: string, signal?: AbortSignal) => Promise<FileBrowserPage>;
  upload?: (
    projectId: string,
    file: File,
    signal: AbortSignal,
  ) => Promise<unknown>;
  rename?: (item: FileBrowserItem, name: string) => Promise<unknown>;
  remove?: (item: FileBrowserItem) => Promise<unknown>;
};

export async function readBrowserFile(
  request: FileRequest,
  item: FileBrowserItem,
  signal: AbortSignal,
): Promise<File> {
  signal.throwIfAborted();
  if (!item.contentUrl) throw Error("此文件没有可读取的原件。");
  const response = await request(item.contentUrl, { signal });
  if (!response.ok) throw Error(`文件读取失败（${response.status}）`);
  const content = await response.blob();
  signal.throwIfAborted();
  return new File([content], item.name, { type: item.mimeType });
}
