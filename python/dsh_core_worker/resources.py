"""Immutable profile-scoped artifacts and resource references."""

import base64
import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from .contracts import CoreError
from .private_files import atomic_private
from .store import encode


class Resources:
    def __init__(self, store, max_bytes=256 * 1024 * 1024):
        self.store, self.max_bytes = store, max_bytes
        self.root = store.root / "artifacts"
        self.root.mkdir(mode=0o700, exist_ok=True)

    def put(self, data: bytes, name: str, mime_type: str, application_id="core", *, identity=None):
        if (
            not isinstance(mime_type, str)
            or not re.fullmatch(r"[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+", mime_type)
            or len(mime_type) > 160
        ):
            raise CoreError("文件媒体类型无效")
        if len(data) > self.max_bytes:
            raise CoreError("文件超过资源大小限制", "resource_too_large")
        digest = hashlib.sha256(data).hexdigest()
        target = self.root / digest
        if not target.exists():
            temporary = self.root / ("." + str(uuid4()))
            try:
                with temporary.open("xb") as output:
                    os.chmod(temporary, 0o600)
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        value = {
            "id": identity or str(uuid4()),
            "sha256": digest,
            "byte_size": len(data),
            "name": Path(name).name[:240],
            "mime_type": mime_type,
            "application_id": application_id,
        }
        previous = self.store.db.execute(
            "SELECT value FROM resources WHERE id=?", (value["id"],)
        ).fetchone()
        if previous:
            if json.loads(previous[0]) != value:
                raise CoreError("资源回执与当前上传不匹配", "upload_conflict")
            return value
        self.store.db.execute("INSERT INTO resources VALUES (?,?)", (value["id"], encode(value)))
        return value

    def describe(self, identity):
        row = self.store.db.execute(
            "SELECT value FROM resources WHERE id=?", (identity,)
        ).fetchone()
        if not row:
            raise CoreError("资源不存在", "not_found")
        return json.loads(row[0])

    def path(self, identity):
        info = self.describe(identity)
        path = self.root / info["sha256"]
        if path.is_symlink() or not path.is_file():
            raise CoreError("资源文件不可用", "resource_unavailable")
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != info["sha256"]:
            raise CoreError("资源文件校验失败", "resource_integrity")
        return path, info

    def read(self, identity, max_bytes=16 * 1024 * 1024):
        path, info = self.path(identity)
        if info["byte_size"] > max_bytes:
            raise CoreError("文件超出单次预览大小", "resource_too_large")
        return {**info, "data_base64": base64.b64encode(path.read_bytes()).decode()}

    def list(self):
        hidden = set(self.store.config("hidden_resources", [])["value"])
        return [
            json.loads(r[0])
            for r in self.store.db.execute(
                "SELECT value FROM resources ORDER BY rowid DESC LIMIT 500"
            )
            if json.loads(r[0])["id"] not in hidden
        ]

    def chunk(self, identity, offset=0, limit=1024 * 1024):
        info = self.describe(identity)
        if (
            not isinstance(offset, int)
            or not 0 <= offset <= info["byte_size"]
            or not 1 <= limit <= 1024 * 1024
        ):
            raise CoreError("资源分块范围无效")
        path = self.path(identity)[0] if offset == 0 else self.root / info["sha256"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != info["byte_size"]:
            raise CoreError("资源文件不可用", "resource_unavailable")
        with path.open("rb") as source:
            source.seek(offset)
            data = source.read(limit)
        return {
            **info,
            "offset": offset,
            "next_offset": offset + len(data),
            "eof": offset + len(data) == info["byte_size"],
            "data_base64": base64.b64encode(data).decode(),
        }

    def upload(self, action, params):
        import shutil
        from uuid import UUID

        directory = self.store.root / "uploads"
        directory.mkdir(exist_ok=True, mode=0o700)
        if action == "begin":
            size = params["byte_size"]
            if not isinstance(size, int) or not 0 <= size <= self.max_bytes:
                raise CoreError("文件超过资源大小限制", "resource_too_large")
            if len(list(directory.iterdir())) >= 4:
                raise CoreError("请先完成或取消现有文件上传", "upload_busy")
            identity = str(uuid4())
            target = directory / identity
            target.mkdir(mode=0o700)
            metadata = {
                "id": identity,
                "resource_id": str(uuid4()),
                "name": Path(params["name"]).name,
                "mime_type": params["mime_type"],
                "byte_size": size,
                "application_id": params.get("application_id", "core"),
            }
            (target / "metadata.json").write_text(encode(metadata))
            (target / "metadata.json").chmod(0o600)
            (target / "data").touch(mode=0o600)
            return {"id": identity, "offset": 0}
        try:
            identity = str(UUID(params["id"]))
        except ValueError:
            raise CoreError("上传编号无效") from None
        receipt = self.store.config("upload-receipt:" + identity, None)
        if receipt["revision"]:
            resource = receipt["value"]
            completed = directory / identity
            if completed.is_dir() and not completed.is_symlink():
                shutil.rmtree(completed)
            if action == "finish":
                return resource
            if action == "status":
                return {
                    **resource,
                    "id": identity,
                    "offset": resource["byte_size"],
                    "completed": True,
                }
            if action == "abort":
                return {"aborted": False, "completed": True}
            raise CoreError("文件上传已完成", "upload_conflict")
        target = directory / identity
        if target.is_symlink() or not (target / "metadata.json").is_file():
            raise CoreError("上传不存在", "not_found")
        metadata = json.loads((target / "metadata.json").read_text())
        path = target / "data"
        if path.is_symlink():
            raise CoreError("上传文件不可用")
        size = path.stat().st_size
        if action == "status":
            return {**metadata, "offset": size}
        if action == "abort":
            shutil.rmtree(target)
            return {"aborted": True}
        if action == "append":
            if len(params["data_base64"]) > 1500000:
                raise CoreError("上传分块过大")
            try:
                data = base64.b64decode(params["data_base64"], validate=True)
            except ValueError:
                raise CoreError("文件编码无效") from None
            offset = params["offset"]
            if (
                not isinstance(offset, int)
                or offset < 0
                or offset > size
                or offset + len(data) > metadata["byte_size"]
            ):
                raise CoreError("上传分块位置不匹配", "upload_conflict")
            if offset < size:
                with path.open("rb") as source:
                    source.seek(offset)
                    previous = source.read(len(data))
                if previous != data:
                    raise CoreError("上传分块内容冲突", "upload_conflict")
                return {"id": identity, "offset": size}
            with path.open("ab") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            return {"id": identity, "offset": size + len(data)}
        if action == "finish":
            if size != metadata["byte_size"]:
                raise CoreError("文件上传尚未完成", "upload_incomplete")
            if "resource_id" not in metadata:
                metadata["resource_id"] = str(uuid4())
                atomic_private(target / "metadata.json", encode(metadata).encode())
            resource = self.put(
                path.read_bytes(),
                metadata["name"],
                metadata["mime_type"],
                metadata["application_id"],
                identity=metadata["resource_id"],
            )
            self.store.save_config("upload-receipt:" + identity, resource, 0)
            shutil.rmtree(target)
            return resource
        raise CoreError("未知上传操作")
