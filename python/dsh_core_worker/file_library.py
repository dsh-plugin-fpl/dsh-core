"""Account-owned file hierarchy and large-original migration, backed by Core resources."""

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid5

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


class LibraryFolder(BaseModel):
    id: str
    parent_id: str | None
    name: str


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


class LibraryPage(BaseModel):
    items: list[LibraryEntry]
    folders: list[LibraryFolder]
    breadcrumbs: list[LibraryFolder]
    next_cursor: str | None


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

    def page(self, folder_id=None, after=None, search="", limit=100):
        if not 1 <= limit <= 200 or len(search) > 200:
            raise CoreError("文件查询范围无效")
        with self.database() as db:
            breadcrumbs = []
            current = folder_id
            visited = set()
            while current:
                if current in visited:
                    raise CoreError("文件夹关系无效")
                visited.add(current)
                row = db.execute(
                    "SELECT id,parent_id,name FROM library_folders WHERE id=?", (current,)
                ).fetchone()
                if row is None:
                    raise CoreError("文件夹不存在", "not_found")
                breadcrumbs.insert(0, dict(row))
                current = row["parent_id"]
            folders = [
                dict(row)
                for row in db.execute(
                    "SELECT id,parent_id,name FROM library_folders WHERE parent_id IS ? ORDER BY name,id",
                    (folder_id,),
                )
            ]
            entries = {
                row["resource_id"]: dict(row) for row in db.execute("SELECT * FROM library_entries")
            }
        hidden = set(self.store.config("hidden_resources", [])["value"])
        result = []
        # Keyset pagination covers the full library; /v1/resources keeps its legacy contract.
        for record in self.store.db.execute(
            "SELECT id,value FROM resources WHERE id>? ORDER BY id", (after or "",)
        ):
            if record["id"] in hidden:
                continue
            value = json.loads(record["value"])
            entry = entries.get(value["id"])
            parent = entry["folder_id"] if entry else None
            if (not search and parent != folder_id) or (
                search and search.casefold() not in value["name"].casefold()
            ):
                continue
            result.append(
                {
                    **value,
                    "folder_id": parent,
                    "source": entry["source"] if entry else None,
                    "metadata": json.loads(entry["metadata"]) if entry else {},
                }
            )
            if len(result) > limit:
                break
        return {
            "items": result[:limit],
            "folders": folders if not search else [],
            "breadcrumbs": breadcrumbs,
            "next_cursor": result[limit - 1]["id"] if len(result) > limit else None,
        }

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
