"""Owner-side read-only active-work validation after normal service stop."""

import sqlite3
from pathlib import Path


def check_current(manager, entries):
    for entry in entries:
        if entry["id"] != "core":
            if manager.provider(entry, "storage-check", "--state-root", entry["root"])["active"]:
                raise ValueError("应用仍有未完成任务，请先处理：" + entry["id"])
        else:
            root = Path(entry["root"])
            for database in [
                root / "state/core.sqlite3",
                *root.glob("accounts/*/state/core.sqlite3"),
            ]:
                if not database.exists():
                    continue
                with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
                    if db.execute(
                        "SELECT COUNT(*) FROM tasks WHERE status NOT IN ('completed','failed','cancelled','interrupted')"
                    ).fetchone()[0]:
                        raise ValueError("Core 仍有未完成任务，请先处理")
