"""Public storage bundle v1 reader and owner-scoped import mechanics.

Each provider passes only its own table declarations and owned target paths.
"""

import base64
import json
import re
import sqlite3
from pathlib import Path
from uuid import UUID, uuid5

from .storage_io import copy_file, digest


def decode(value):
    if isinstance(value, dict) and set(value) == {"$bytes"}:
        return base64.b64decode(value["$bytes"], validate=True)
    return value


def read_component(bundle, name, allowed=None):
    root = Path(bundle) / name
    manifest = json.loads((root / "manifest.json").read_text())
    declaration = Path(bundle) / "manifest.json"
    if declaration.exists():
        header = json.loads(declaration.read_text())
        if "components" in header and header["components"].get(name) != manifest:
            raise ValueError("组件清单与迁移包声明不一致")
    rows = {}
    for table, record in manifest["datasets"].items():
        if not re.fullmatch(r"[a-zA-Z0-9_]+", table) or (
            allowed is not None and table not in allowed
        ):
            raise ValueError("迁移包含不属于此组件的数据集")
        path = root / (table + ".jsonl")
        if path.is_symlink() or digest(path) != record["sha256"]:
            raise ValueError("迁移数据集摘要不匹配")
        records = []
        with path.open() as stream:
            for line in stream:
                if len(line) > 64 * 1024**2:
                    raise ValueError("迁移记录超过上限")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise TypeError("迁移记录格式无效")
                records.append({key: decode(item) for key, item in value.items()})
        if len(records) != record["count"]:
            raise ValueError("迁移记录数量不匹配")
        rows[table] = records
    return manifest, rows


def translated(value, identifiers, prefixes=()):
    if isinstance(value, dict):
        prose = {
            "text",
            "content",
            "search_text",
            "title",
            "description",
            "label",
            "name",
            "filename",
            "original_filename",
            "display_name",
            "prompt",
            "system_prompt",
            "error_message",
            "status_message",
        }
        return {
            identifiers.get(key, key): (
                item
                if key in prose and isinstance(item, str)
                else translated(item, identifiers, prefixes)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [translated(item, identifiers, prefixes) for item in value]
    if not isinstance(value, str):
        return value
    if value in identifiers:
        return identifiers[value]
    if value.startswith(("{", "[")):
        try:
            parsed = json.loads(value)
        except ValueError:
            pass
        else:
            return json.dumps(
                translated(parsed, identifiers, prefixes), ensure_ascii=False, separators=(",", ":")
            )
    for source, target in prefixes:
        if value == source or value.startswith(source + "/"):
            value = target + value[len(source) :]
            return "/".join(identifiers.get(part, part) for part in value.split("/"))
    return value


def row_equal(left, right):
    if left.keys() != right.keys():
        return False
    for key in left:
        a, b = left[key], right[key]
        if isinstance(a, str) and isinstance(b, str) and a.startswith(("{", "[")):
            try:
                if json.loads(a) == json.loads(b):
                    continue
            except ValueError:
                pass
        if a != b:
            return False
    return True


def plan_identifiers(database, models, records, identifiers, source_id):
    """Allocate source aliases before importing any component or file bytes."""
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        for model in models:
            table = model.__table__
            primary = [column.name for column in table.primary_key]
            if primary != ["id"]:
                continue
            for original in records.get(table.name, []):
                value = translated(original, identifiers)
                existing = db.execute(
                    'SELECT * FROM "' + table.name + '" WHERE id=?', (value["id"],)
                ).fetchone()
                if existing is not None and not row_equal(dict(existing), value):
                    identifiers[original["id"]] = str(
                        uuid5(UUID(source_id), table.name + ":" + original["id"])
                    )


def merge_owned(database, models, records, identifiers, prefixes=(), rekey=None):
    allowed = {model.__tablename__: model.__table__ for model in models}
    counts = {}
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        db.execute("PRAGMA defer_foreign_keys=ON")
        try:
            for name, originals in records.items():
                table = allowed[name]
                primary = [column.name for column in table.primary_key]
                if not primary:
                    raise ValueError("迁移表缺少明确主键")
                columns = {column.name for column in table.columns}
                for original in originals:
                    if set(original) - columns:
                        raise ValueError("来源与目标字段版本不兼容：" + name)
                    row = translated(original, identifiers, prefixes)
                    if rekey is not None:
                        row = rekey(row)
                    where = " AND ".join('"' + key + '" IS ?' for key in primary)
                    previous = db.execute(
                        'SELECT * FROM "' + name + '" WHERE ' + where, [row[key] for key in primary]
                    ).fetchone()
                    if previous is not None:
                        if not row_equal({key: previous[key] for key in row}, row):
                            raise ValueError("迁移记录冲突，原记录未被覆盖：" + name)
                        continue
                    keys = list(row)
                    db.execute(
                        'INSERT INTO "'
                        + name
                        + '" ('
                        + ",".join('"' + key + '"' for key in keys)
                        + ") VALUES ("
                        + ",".join("?" for _ in keys)
                        + ")",
                        [row[key] for key in keys],
                    )
                counts[name] = len(originals)
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("迁入后的引用关系不完整")
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return counts


def copy_component_files(bundle, component, destination, manifest, identifiers):
    root = Path(bundle) / component / "files"
    count = 0
    for relative, record in manifest["files"].items():
        value = Path(relative)
        if value.is_absolute() or ".." in value.parts:
            raise ValueError("迁移文件路径越界")
        source = root / value
        if not source.resolve().is_relative_to(root.resolve()):
            raise ValueError("迁移源文件路径越界")
        path = Path(*[identifiers.get(part, part) for part in value.parts])
        target = Path(destination) / path
        if not target.resolve().is_relative_to(Path(destination).resolve()):
            raise ValueError("迁移目标文件路径越界")
        copy_file(source, target, record["sha256"])
        count += 1
    return count
