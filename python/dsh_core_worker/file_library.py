"""Account-owned file hierarchy and large-original migration, backed by Core resources."""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, Field

from .contracts import CoreError
from .identity_schema import Migration, apply, check
from .storage_io import copy_file
from .store import encode

MIGRATIONS = (
    Migration(
        "file-library-v1",
        """
CREATE TABLE library_folders(id TEXT PRIMARY KEY,parent_id TEXT,name TEXT NOT NULL,source TEXT NOT NULL,metadata TEXT NOT NULL);
CREATE TABLE library_entries(resource_id TEXT PRIMARY KEY,folder_id TEXT,source TEXT NOT NULL,metadata TEXT NOT NULL);
CREATE INDEX library_folder_parent ON library_folders(parent_id,name);
CREATE INDEX library_entry_folder ON library_entries(folder_id,resource_id);
""",
    ),
)


def media_kind(mime):
    prefix = mime.split("/", 1)[0]
    if prefix in ("image", "video", "audio"):
        return prefix
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("text/") or mime in ("application/json", "application/xml"):
        return "text"
    return "file"


def presentation(metadata):
    return {
        "revision": metadata.get("core_library_revision", 1),
        "updated_at": metadata.get("core_library_updated_at")
        or metadata.get("updated_at")
        or metadata.get("created_at"),
    }


def valid_name(value):
    value = value.strip()
    if (
        not value
        or len(value) > 240
        or any(c in value for c in ("/", "\\", "\x00"))
        or value in (".", "..")
    ):
        raise CoreError("名称须为 1–240 个字符，不能包含路径分隔符")
    return value


class LibraryFolder(BaseModel):
    id: str
    parent_id: str | None
    name: str
    revision: int = 1
    updated_at: str | None = None


class LibraryEntry(BaseModel):
    id: str
    name: str
    mime_type: str
    byte_size: int
    sha256: str
    application_id: str
    folder_id: str | None = None
    source: str | None = None
    metadata: dict = Field(default_factory=dict)
    revision: int = 1
    updated_at: str | None = None
    media_kind: str = "file"


class LibraryPage(BaseModel):
    items: list[LibraryEntry]
    folders: list[LibraryFolder]
    breadcrumbs: list[LibraryFolder]
    next_cursor: str | None
    folder_count: int = 0
    file_count: int = 0


class FileLibrary:
    def __init__(self, resources, *, initialize=False):
        self.resources, self.store = resources, resources.store
        self.path = self.store.root / "file-library.sqlite3"
        if initialize:
            apply(self.path, "core-file-library", MIGRATIONS)

    @contextmanager
    def database(self):
        check(self.path, "core-file-library", MIGRATIONS)
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def folder_value(row):
        return {**dict(row), **presentation(json.loads(row["metadata"]))}

    def entry_value(self, value, entry):
        metadata = json.loads(entry["metadata"]) if entry else {}
        return {
            **value,
            "name": metadata.get("core_library_name") or value["name"],
            "folder_id": entry["folder_id"] if entry else None,
            "source": entry["source"] if entry else None,
            "metadata": metadata,
            **presentation(metadata),
            "media_kind": media_kind(value["mime_type"]),
        }

    def page(self, folder_id=None, after=None, search="", limit=100, kind="all", sort="updated"):
        if not 1 <= limit <= 200 or len(search) > 200:
            raise CoreError("文件查询范围无效")
        if kind not in ("all", "image", "video", "audio", "pdf", "text", "file") or sort not in (
            "updated",
            "name",
            "size",
        ):
            raise CoreError("筛选或排序无效")
        search = search.strip().casefold()
        with self.database() as db:
            breadcrumbs = []
            current = folder_id
            visited = set()
            while current:
                if current in visited:
                    raise CoreError("文件夹关系无效")
                visited.add(current)
                row = self.require_folder(db, current)
                breadcrumbs.insert(0, self.folder_value(row))
                current = row["parent_id"]
            folders = [
                self.folder_value(row)
                for row in db.execute(
                    "SELECT * FROM library_folders WHERE parent_id IS ?", (folder_id,)
                )
            ]
            if search:
                folders = [
                    self.folder_value(row)
                    for row in db.execute("SELECT * FROM library_folders")
                    if search in row["name"].casefold()
                ]
            entries = {
                row["resource_id"]: dict(row) for row in db.execute("SELECT * FROM library_entries")
            }
        hidden = set(self.store.config("hidden_resources", [])["value"])
        result = []
        for record in self.store.db.execute("SELECT id,value FROM resources ORDER BY rowid DESC"):
            if record["id"] in hidden:
                continue
            value = self.entry_value(json.loads(record["value"]), entries.get(record["id"]))
            if value["metadata"].get("core_library_hidden"):
                continue
            if (not search and value["folder_id"] != folder_id) or (
                search and search not in value["name"].casefold()
            ):
                continue
            if kind != "all" and value["media_kind"] != kind:
                continue
            result.append(value)
        if kind != "all":
            folders = []
        if sort == "name":
            key = lambda item: (item["name"].casefold(), item["id"])
            result.sort(key=key)
            folders.sort(key=key)
        elif sort == "size":
            result.sort(key=lambda item: (item["byte_size"], item["id"]), reverse=True)
            folders.sort(key=lambda item: (item["name"].casefold(), item["id"]))
        else:
            # Keep insertion order for resources without historical timestamps.
            result.sort(key=lambda item: item["updated_at"] or "", reverse=True)
            folders.sort(
                key=lambda item: (item["updated_at"] or "", item["name"].casefold()), reverse=True
            )
        file_count = len(result)
        if after:
            index = next((i for i, item in enumerate(result) if item["id"] == after), None)
            if index is None:
                raise CoreError("文件列表已变化，请返回第一页", "revision_conflict")
            result = result[index + 1 :]
        return {
            "items": result[:limit],
            "folders": folders,
            "breadcrumbs": breadcrumbs,
            "next_cursor": result[limit - 1]["id"] if len(result) > limit else None,
            "folder_count": len(folders),
            "file_count": file_count,
        }

    @staticmethod
    def require_folder(db, identity):
        row = db.execute("SELECT * FROM library_folders WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise CoreError("文件夹不存在", "not_found")
        return row

    def check_parent(self, db, identity, parent_id):
        seen = {identity}
        while parent_id:
            if parent_id in seen:
                raise CoreError("不能将文件夹移入自身或子文件夹")
            seen.add(parent_id)
            parent_id = self.require_folder(db, parent_id)["parent_id"]

    @staticmethod
    def next_metadata(metadata, revision):
        current = metadata.get("core_library_revision", 1)
        if current != revision:
            raise CoreError("文件已被修改，请刷新后重试", "revision_conflict")
        return {
            **metadata,
            "core_library_revision": current + 1,
            "core_library_updated_at": datetime.now(UTC).isoformat(),
        }

    def check_duplicate(self, db, name, parent, identity=None):
        for row in db.execute(
            "SELECT id,name FROM library_folders WHERE parent_id IS ?", (parent,)
        ):
            if row["id"] != identity and row["name"].casefold() == name.casefold():
                raise CoreError("目标位置已有同名文件夹", "revision_conflict")

    def create_folder(self, name, parent_id=None, identity=None):
        identity, name = identity or str(uuid4()), valid_name(name)
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM library_folders WHERE id=?", (identity,)
            ).fetchone()
            if existing:
                if existing["parent_id"] == parent_id and existing["name"] == name:
                    return self.folder_value(existing)
                raise CoreError("导入目录已被修改，请先核对已导入内容", "revision_conflict")
            self.check_parent(db, identity, parent_id)
            self.check_duplicate(db, name, parent_id)
            metadata = {
                "core_library_revision": 1,
                "core_library_updated_at": datetime.now(UTC).isoformat(),
            }
            db.execute(
                "INSERT INTO library_folders VALUES(?,?,?,?,?)",
                (identity, parent_id, name, "core", encode(metadata)),
            )
            return self.folder_value(self.require_folder(db, identity))

    def patch_folder(self, identity, revision, changes):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.require_folder(db, identity)
            metadata = self.next_metadata(json.loads(row["metadata"]), revision)
            name = valid_name(changes.get("name", row["name"]))
            parent = changes.get("parent_id", row["parent_id"])
            self.check_parent(db, identity, parent)
            self.check_duplicate(db, name, parent, identity)
            db.execute(
                "UPDATE library_folders SET name=?,parent_id=?,metadata=? WHERE id=?",
                (name, parent, encode(metadata), identity),
            )
            return self.folder_value(self.require_folder(db, identity))

    def delete_folder(self, identity, revision):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.require_folder(db, identity)
            self.next_metadata(json.loads(row["metadata"]), revision)
            if db.execute(
                "SELECT 1 FROM library_folders WHERE parent_id=?", (identity,)
            ).fetchone() or any(
                not json.loads(row[0]).get("core_library_hidden")
                for row in db.execute(
                    "SELECT metadata FROM library_entries WHERE folder_id=?", (identity,)
                )
            ):
                raise CoreError("请先移走或移除文件夹内的内容，再删除空文件夹")
            db.execute("DELETE FROM library_folders WHERE id=?", (identity,))
        return {"deleted": True}

    def entry(self, identity):
        value = self.resources.describe(identity)
        if identity in set(self.store.config("hidden_resources", [])["value"]):
            raise CoreError("文件不存在", "not_found")
        with self.database() as db:
            row = db.execute(
                "SELECT * FROM library_entries WHERE resource_id=?", (identity,)
            ).fetchone()
            result = self.entry_value(value, row)
        if result["metadata"].get("core_library_hidden"):
            raise CoreError("文件已从文件管理移除", "not_found")
        return result

    def patch_entry(self, identity, revision, changes, *, remove=False):
        value = self.resources.describe(identity)
        if identity in set(self.store.config("hidden_resources", [])["value"]):
            raise CoreError("文件不存在", "not_found")
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM library_entries WHERE resource_id=?", (identity,)
            ).fetchone()
            metadata = json.loads(row["metadata"]) if row else {}
            if metadata.get("core_library_hidden"):
                raise CoreError("文件已从文件管理移除", "not_found")
            metadata = self.next_metadata(metadata, revision)
            if "name" in changes:
                metadata["core_library_name"] = valid_name(changes["name"])
            if remove:
                metadata["core_library_hidden"] = True
            parent = changes.get("folder_id", row["folder_id"] if row else None)
            if parent:
                self.require_folder(db, parent)
            db.execute(
                "INSERT INTO library_entries VALUES(?,?,?,?) ON CONFLICT(resource_id) DO UPDATE SET folder_id=excluded.folder_id,metadata=excluded.metadata",
                (identity, parent, row["source"] if row else "core", encode(metadata)),
            )
            return self.entry_value(
                value,
                {
                    "folder_id": parent,
                    "source": row["source"] if row else "core",
                    "metadata": encode(metadata),
                },
            )

    def import_folders(self, rows, source_id):
        with self.database() as db:
            for row in rows:
                previous = db.execute(
                    "SELECT * FROM library_folders WHERE id=?", (row["id"],)
                ).fetchone()
                if previous and previous["source"] != source_id:
                    raise CoreError("文件夹标识与现有目录冲突", "storage_conflict")
                db.execute(
                    "INSERT INTO library_folders VALUES(?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                    (row["id"], row.get("parent_id"), row["name"], source_id, encode(row)),
                )

    def import_original(self, row, source, digest, source_id):
        identity = row["id"]
        UUID(identity)
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or row["byte_size"] < 0:
            raise CoreError("迁移原件描述无效", "storage_conflict")
        value = {
            "id": identity,
            "name": row.get("display_name") or row["original_filename"],
            "mime_type": row["mime_type"],
            "byte_size": row["byte_size"],
            "sha256": digest,
            "application_id": "core",
        }
        previous = self.store.db.execute(
            "SELECT value FROM resources WHERE id=?", (identity,)
        ).fetchone()
        if previous and json.loads(previous[0]) != value:
            identity = str(uuid5(UUID(source_id), "core-resource:" + identity))
            value["id"] = identity
            previous = self.store.db.execute(
                "SELECT value FROM resources WHERE id=?", (identity,)
            ).fetchone()
        if previous and json.loads(previous[0]) != value:
            raise CoreError("原件与迁移回执不一致", "storage_conflict")
        if Path(source).stat().st_size != value["byte_size"]:
            raise CoreError("原件长度不一致", "storage_conflict")
        # This explicit owner maintenance path streams large originals without
        # changing the 256MB limit of normal plugin resource uploads.
        copy_file(source, self.resources.root / digest, digest)
        self.store.db.execute(
            "INSERT INTO resources VALUES(?,?) ON CONFLICT(id) DO NOTHING",
            (identity, encode(value)),
        )
        with self.database() as db:
            db.execute(
                "INSERT INTO library_entries VALUES(?,?,?,?) ON CONFLICT(resource_id) DO NOTHING",
                (identity, row.get("folder_id"), source_id, encode(row)),
            )
        return value
