"""Bounded, resumable library imports and streaming ZIP exports over owned resources."""

import hashlib
import json
import mimetypes
import stat
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import UUID, uuid5

from .contracts import CoreError
from .file_library import valid_name
from .store import encode

MAX_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 5000


def relative_path(value):
    if not value or "\\" in value or value.startswith("/") or "\x00" in value:
        raise CoreError("归档包含不安全的相对路径")
    parts = value.rstrip("/").split("/")
    if len(parts) > 32 or any(part in ("", ".", "..") or ":" in part for part in parts):
        raise CoreError("归档包含不安全的相对路径")
    return "/".join(valid_name(part) for part in parts)


def zip_members(archive):
    rows, total, seen = [], 0, set()
    for member in archive.infolist():
        path = relative_path(member.filename)
        if path.casefold() in seen:
            raise CoreError("归档包含重复路径")
        seen.add(path.casefold())
        if stat.S_ISLNK(member.external_attr >> 16) or member.flag_bits & 1:
            raise CoreError("不支持符号链接或加密归档")
        total += member.file_size
        if len(seen) > MAX_ENTRIES or total > MAX_BYTES:
            raise CoreError("单次最多导入 5000 项、解压后合计 256 MB")
        rows.append((path, member))
    validate_paths([(path, member.is_dir()) for path, member in rows])
    return rows


def validate_paths(rows):
    files = {path for path, directory in rows if not directory}
    all_paths = {path for path, _ in rows}
    for path, _ in rows:
        for parent in PurePosixPath(path).parents:
            if str(parent) == ".":
                continue
            if str(parent) in files:
                raise CoreError("导入中的文件与文件夹路径冲突")
            all_paths.add(str(parent))
    if len(all_paths) > MAX_ENTRIES:
        raise CoreError("包含父文件夹后，单次最多导入 5000 项")


class LibraryImport:
    def __init__(self, library, request_id, parent_id, fingerprint):
        self.library = library
        self.identity = UUID(request_id)
        self.parent_id = parent_id
        self.key = "library-import:" + str(self.identity)
        self.fingerprint = fingerprint
        receipt = library.store.config(self.key, None)
        if receipt["revision"] and (
            receipt["value"]["fingerprint"] != fingerprint
            or receipt["value"]["parent_id"] != parent_id
        ):
            raise CoreError("导入回执与文件内容不一致", "idempotency_conflict")
        self.receipt = receipt
        self.paths = {"": parent_id}
        self.folders, self.files = 0, 0
        with library.database() as db:
            if parent_id:
                library.require_folder(db, parent_id)
        if not receipt["revision"]:
            self.receipt = library.store.save_config(
                self.key,
                {"fingerprint": fingerprint, "parent_id": parent_id, "status": "running"},
                0,
            )

    @property
    def completed(self):
        return self.receipt["value"].get("result")

    def folder(self, path):
        if path in self.paths:
            return self.paths[path]
        parent, _, name = path.rpartition("/")
        parent_id = self.folder(parent)
        with self.library.database() as db:
            row = next(
                (
                    row
                    for row in db.execute(
                        "SELECT * FROM library_folders WHERE parent_id IS ?", (parent_id,)
                    )
                    if row["name"].casefold() == name.casefold()
                ),
                None,
            )
        if row:
            identity = row["id"]
        else:
            identity = str(uuid5(self.identity, "folder:" + path))
            self.library.create_folder(name, parent_id, identity)
        self.paths[path] = identity
        self.folders += 1
        return identity

    def file(self, path, data, mime=None):
        parent, _, name = path.rpartition("/")
        parent_id = self.folder(parent)
        identity = str(uuid5(self.identity, "file:" + path))
        self.library.resources.put(
            data,
            name,
            mime or mimetypes.guess_type(name)[0] or "application/octet-stream",
            identity=identity,
        )
        metadata = {
            "core_library_revision": 1,
            "core_library_updated_at": datetime.now(UTC).isoformat(),
        }
        with self.library.database() as db:
            if parent_id:
                self.library.require_folder(db, parent_id)
            db.execute(
                "INSERT INTO library_entries VALUES(?,?,?,?) ON CONFLICT(resource_id) DO NOTHING",
                (identity, parent_id, "import:" + str(self.identity), encode(metadata)),
            )
        self.files += 1

    def finish(self):
        result = {"files": self.files, "folders": self.folders, "request_id": str(self.identity)}
        current = self.library.store.config(self.key)
        self.library.store.save_config(
            self.key,
            {**current["value"], "status": "completed", "result": result},
            current["revision"],
        )
        return result


def export_manifest(library, folder_id):
    with library.database() as db:
        folders = {row["id"]: dict(row) for row in db.execute("SELECT * FROM library_folders")}
        entries = {
            row["resource_id"]: dict(row) for row in db.execute("SELECT * FROM library_entries")
        }
        if folder_id:
            library.require_folder(db, folder_id)
    paths = {folder_id: ""}

    def path_for(identity, seen=None):
        if identity in paths:
            return paths[identity]
        if identity not in folders:
            return None
        seen = set() if seen is None else set(seen)
        if identity in seen:
            raise CoreError("文件夹关系无效")
        seen.add(identity)
        row = folders[identity]
        parent = path_for(row["parent_id"], seen)
        if parent is None:
            return None
        path = str(PurePosixPath(parent) / valid_name(row["name"]))
        paths[identity] = unique(path)
        return paths[identity]

    names, result = set(), []

    def unique(path):
        name = PurePosixPath(path)
        candidate, number = path, 1
        while candidate.casefold() in names:
            number += 1
            candidate = str(name.with_name(f"{name.stem} ({number}){name.suffix}"))
        names.add(candidate.casefold())
        return candidate

    for identity in folders:
        path = path_for(identity)
        if path:
            result.append((path + "/", None, None))
    hidden = set(library.store.config("hidden_resources", [])["value"])
    for row in library.store.db.execute("SELECT id,value FROM resources"):
        if row["id"] in hidden:
            continue
        value = library.entry_value(json.loads(row["value"]), entries.get(row["id"]))
        if value["metadata"].get("core_library_hidden"):
            continue
        parent = path_for(value["folder_id"])
        if parent is not None:
            path = unique(str(PurePosixPath(parent) / valid_name(value["name"])))
            result.append((path, library.resources.root / value["sha256"], value["sha256"]))
    directory = library.store.root / "library-transfers"
    directory.mkdir(mode=0o700, exist_ok=True)
    return result, directory


def build_export(manifest, directory):
    stream = tempfile.TemporaryFile(dir=directory)  # noqa: SIM115 - response owns this stream
    try:
        with zipfile.ZipFile(
            stream, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as archive:
            for name, path, digest in manifest:
                if path is None:
                    archive.writestr(name, b"")
                    continue
                if path.is_symlink() or not path.is_file():
                    raise CoreError("资源文件不可用")
                check = hashlib.sha256()
                with path.open("rb") as source, archive.open(name, "w", force_zip64=True) as target:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        check.update(chunk)
                        target.write(chunk)
                if check.hexdigest() != digest:
                    raise CoreError("资源文件校验失败")
        stream.seek(0)
        return stream
    except BaseException:
        stream.close()
        raise
