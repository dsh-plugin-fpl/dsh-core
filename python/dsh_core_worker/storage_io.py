"""Owned storage relocation primitives: bounded copying, integrity and durable receipts."""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

from .private_files import atomic_private

IGNORED = frozenset(
    {
        "__pycache__",
        ".DS_Store",
        "launcher.lock",
        "service.json",
        "service.log",
        "application.log",
        "process.json",
        ".service",
        ".storage-paused",
    }
)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def directories(parent, dirs):
    parent = Path(parent)
    return [
        name
        for name in dirs
        if name not in IGNORED
        and not (name == "environments" and parent.name == "local-models")
        and not (
            name == "uv-cache"
            and parent.name == "downloads"
            and parent.parent.name == "local-models"
        )
    ]


def inventory(root):
    root = Path(root)
    files, size = 0, 0
    if root.exists():
        for parent, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = directories(parent, dirs)
            for name in dirs + names:
                path = Path(parent) / name
                if path.is_symlink():
                    raise ValueError("运行数据包含符号链接，需先由所属安装器处理：" + str(path))
            for name in names:
                if name in IGNORED or name.endswith(("-wal", "-shm")):
                    continue
                files += 1
                size += (Path(parent) / name).stat().st_size
    return {"files": files, "bytes": size}


def copy_file(source, target, expected=None):
    source, target = Path(source), Path(target)
    if source.is_symlink() or not source.is_file() or target.is_symlink():
        raise ValueError("迁移只接受普通文件")
    expected = expected or digest(source)
    if target.exists():
        if digest(target) != expected:
            raise ValueError("目标文件与迁移回执不一致：" + str(target))
        return expected
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name("." + target.name + ".incoming")
    if temporary.exists():
        temporary.unlink()
    # APFS clones are separate files/inodes, with no dependency on the source path.
    copied = False
    if os.uname().sysname == "Darwin":
        copied = (
            subprocess.run(
                ["/bin/cp", "-c", str(source), str(temporary)], check=False, capture_output=True
            ).returncode
            == 0
        )
    if not copied:
        with source.open("rb") as incoming, temporary.open("wb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
    os.chmod(temporary, 0o600)
    if digest(temporary) != expected:
        raise ValueError("原件复制摘要不一致：" + str(source))
    with temporary.open("rb") as ready:
        os.fsync(ready.fileno())
    os.replace(temporary, target)
    directory = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return expected


def snapshot_database(source, target):
    """Called by the database owner after its writer has stopped."""
    source, target = Path(source), Path(target)
    if target.exists():
        with sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("数据库快照已损坏")
        return
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (
        closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as incoming,
        closing(sqlite3.connect(target)) as outgoing,
    ):
        incoming.backup(outgoing)
        if outgoing.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("数据库快照完整性检查失败")
    os.chmod(target, 0o600)


def copy_tree(source, target, receipt):
    source, target, receipt = Path(source), Path(target), Path(receipt)
    saved = json.loads(receipt.read_text()) if receipt.exists() else {}
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    for parent, dirs, names in os.walk(source, followlinks=False):
        dirs[:] = directories(parent, dirs)
        for name in dirs + names:
            if (Path(parent) / name).is_symlink():
                raise ValueError("数据目录中的符号链接未获迁移许可")
        for name in names:
            if name in IGNORED or name.endswith(("-wal", "-shm")):
                continue
            path = Path(parent) / name
            relative = str(path.relative_to(source))
            dest = target / relative
            if name.endswith((".db", ".sqlite", ".sqlite3")):
                snapshot_database(path, dest)
                checksum = digest(dest)
            else:
                checksum = copy_file(path, dest, saved.get(relative))
            if relative in saved and saved[relative] != checksum:
                raise ValueError("迁移快照与回执不一致")
            saved[relative] = checksum
            if len(saved) % 100 == 0:
                atomic_private(receipt, json.dumps(saved, sort_keys=True).encode())
    atomic_private(receipt, json.dumps(saved, sort_keys=True).encode())
    return {
        "files": len(saved),
        "sha256": hashlib.sha256(json.dumps(saved, sort_keys=True).encode()).hexdigest(),
    }


def validate_target(target, sources):
    target = Path(target).expanduser()
    if not target.is_absolute():
        raise ValueError("请选择绝对存储路径")
    if target.is_symlink() or ".local" in target.parts:
        raise ValueError("不能使用符号链接或临时目录保存正式数据")
    target = target.resolve()
    if ".local" in target.parts:
        raise ValueError("不能使用临时目录保存正式数据")
    for source in (Path(p).resolve() for p in sources):
        if target == source or target.is_relative_to(source) or source.is_relative_to(target):
            raise ValueError("新存储目录不能与现有运行目录重叠")
    if target.exists() and any(target.iterdir()):
        raise ValueError("目标目录非空，请选择空目录；已有迁移请使用恢复入口")
    ancestor = target
    while not ancestor.exists():
        ancestor = ancestor.parent
    if not os.access(ancestor, os.W_OK):
        raise ValueError("目标目录不可写")
    return target, shutil.disk_usage(ancestor).free
