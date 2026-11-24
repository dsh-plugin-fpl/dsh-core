import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchApiJson, requestApi } from "@platform/api/client";
import { TextInputDialog } from "@shared/ui/TextInputDialog";
import { ConfirmDialog } from "@shared/ui/ConfirmDialog";
import { DetailDialog } from "@shared/ui/DetailDialog";
import { CustomSelect } from "@shared/ui/CustomSelect";
import {
  folderOptions,
  itemPath,
  libraryKey,
  patchItem,
  type Editor,
  type LibraryFolder,
} from "./fileLibrary";
export function FileEditor({
  editor,
  folderId,
  close,
  saved,
}: {
  editor: Editor;
  folderId: string | null;
  close: () => void;
  saved: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [target, setTarget] = useState(
    editor.action === "move"
      ? (editor.item.type === "folder"
          ? editor.item.value.parent_id
          : editor.item.value.folder_id) || ""
      : "",
  );
  const folders = useQuery({
    queryKey: [...libraryKey, "folders"],
    queryFn: ({ signal }) =>
      fetchApiJson<LibraryFolder[]>("/v1/file-library/folders", { signal }),
    enabled: editor.action === "move",
  });
  const run = async (action: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
      await saved();
      close();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (editor.action === "create" || editor.action === "rename")
    return (
      <TextInputDialog
        open
        title={editor.action === "create" ? "新建文件夹" : "重命名"}
        label="名称"
        initialValue={editor.action === "rename" ? editor.item.value.name : ""}
        maxLength={240}
        pending={busy}
        error={error}
        onCancel={close}
        onConfirm={(name) =>
          void run(() =>
            editor.action === "create"
              ? fetchApiJson("/v1/file-library/folders", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ name, parent_id: folderId }),
                })
              : patchItem(editor.item, { name }),
          )
        }
      />
    );
  if (editor.action === "delete")
    return (
      <ConfirmDialog
        open
        title={
          editor.item.type === "folder" ? "删除空文件夹？" : "从文件管理移除？"
        }
        description={
          <>
            <p>{editor.item.value.name}</p>
            <p>
              {editor.item.type === "folder"
                ? "文件夹必须为空，此操作无法撤销。"
                : "此文件将不再出现在文件管理中。已被工坊引用的原件会保留。"}
            </p>
            {error && <p role="alert">{error}</p>}
          </>
        }
        pending={busy}
        onCancel={close}
        onConfirm={() =>
          run(() =>
            requestApi(
              `${itemPath(editor.item)}?expected_revision=${editor.item.value.revision}`,
              { method: "DELETE" },
            ),
          )
        }
      />
    );
  return (
    <DetailDialog
      title="移动到文件夹"
      onClose={() => {
        if (!busy) close();
      }}
    >
      <form
        className="file-move-form"
        onSubmit={(event) => {
          event.preventDefault();
          void run(() =>
            patchItem(editor.item, {
              [editor.item.type === "folder" ? "parent_id" : "folder_id"]:
                target || null,
            }),
          );
        }}
      >
        <p>{editor.item.value.name}</p>
        <CustomSelect
          ariaLabel="目标文件夹"
          value={target}
          disabled={busy || folders.isPending || !!folders.error}
          options={folderOptions(
            folders.data || [],
            editor.item.type === "folder" ? editor.item.value : undefined,
          )}
          onChange={setTarget}
        />
        {(error || folders.error) && (
          <p role="alert">{error || folders.error?.message}</p>
        )}
        <button
          className="btn primary"
          type="submit"
          disabled={busy || folders.isPending || !!folders.error}
        >
          {busy ? "移动中…" : "移动"}
        </button>
      </form>
    </DetailDialog>
  );
}
