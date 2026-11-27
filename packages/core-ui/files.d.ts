import type { ReactElement } from "react";
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

export function createFileBrowserAdapter(options: {
  id: string;
  request: FileRequest;
  projects: (signal?: AbortSignal) => Promise<FileBrowserProject[]>;
  projectFiles: NativeProjectFiles;
}): FileBrowserAdapter;
export function browserFile(
  row: Record<string, unknown>,
  source: FileBrowserItem["source"],
  projectId?: string,
): FileBrowserItem;
export function browserJson<T>(
  request: FileRequest,
  path: string,
  init?: RequestInit,
): Promise<T>;
export function documentAssetIds(document: unknown): Set<string>;
export function referencedBrowserFiles(
  request: FileRequest,
  documents: unknown[],
  projectId: string,
  signal?: AbortSignal,
): Promise<FileBrowserItem[]>;
export type ProductFilePickerProps = {
  request?: FileRequest;
  adapter?: FileBrowserAdapter;
  projectId?: string;
  accept?: string;
  disabled?: boolean;
  multiple?: boolean;
  onChoose?: (file: File, signal: AbortSignal) => Promise<unknown> | void;
  onSelect?: (
    items: FileBrowserItem[],
    signal: AbortSignal,
  ) => Promise<unknown> | void;
  label?: string;
};
export function ProductFilePicker(props: ProductFilePickerProps): ReactElement;

export function readBrowserFile(
  request: FileRequest,
  item: FileBrowserItem,
  signal: AbortSignal,
): Promise<File>;
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
export function SharedFileBrowser(props: SharedFileBrowserProps): ReactElement;
