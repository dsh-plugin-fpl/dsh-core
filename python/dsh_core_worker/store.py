"""Profile-owned SQLite state and durable idempotent task admission."""

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

from .contracts import CoreError

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def encode(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.db = sqlite3.connect(self.root / "core.sqlite3", isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=30000")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise CoreError("运行数据版本与当前插件不匹配", "state_version")
        if version == 0:
            self.db.executescript("""BEGIN IMMEDIATE;
CREATE TABLE configs (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, value TEXT NOT NULL);
CREATE TABLE tasks (id TEXT PRIMARY KEY, scope TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL, value TEXT NOT NULL, UNIQUE(scope,request_id));
CREATE TABLE resources (id TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, created REAL NOT NULL);
PRAGMA user_version=1;
COMMIT;""")
        os.chmod(self.root / "core.sqlite3", 0o600)

    def config(self, key, initial=None):
        row = self.db.execute("SELECT revision,value FROM configs WHERE id=?", (key,)).fetchone()
        return (
            {"revision": row[0], "value": json.loads(row[1])}
            if row
            else {"revision": 0, "value": initial if initial is not None else {}}
        )

    def save_config(self, key, value, expected):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            current = self.config(key)
            if expected != current["revision"]:
                raise CoreError("配置已更新，请刷新后保存", "revision_conflict", current=current)
            revision = expected + 1
            self.db.execute(
                "INSERT INTO configs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,value=excluded.value",
                (key, revision, encode(value)),
            )
            self.db.execute("COMMIT")
            return {"revision": revision, "value": value}
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def replay(self, request):
        body = request.model_dump(mode="json")
        scope = encode([request.application_id, request.session_id])
        row = self.db.execute(
            "SELECT fingerprint,value FROM tasks WHERE scope=? AND request_id=?",
            (scope, request.request_id),
        ).fetchone()
        if not row:
            return None
        if row[0] != hashlib.sha256(encode(body).encode()).hexdigest():
            raise CoreError("请求编号已用于其他输入", "idempotency_conflict")
        return json.loads(row[1])

    def admit(self, request, snapshot, *, fingerprint_body=None):
        body = request.model_dump(mode="json")
        scope = encode([request.application_id, request.session_id])
        fingerprint = hashlib.sha256(
            encode(fingerprint_body if fingerprint_body is not None else body).encode()
        ).hexdigest()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT fingerprint,value FROM tasks WHERE scope=? AND request_id=?",
                (scope, request.request_id),
            ).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise CoreError("请求编号已用于其他输入", "idempotency_conflict")
                self.db.execute("COMMIT")
                return json.loads(row[1]), False
            value = {
                "id": str(uuid4()),
                "request": body,
                "connection": snapshot,
                "status": "queued",
                "progress": 0,
                "text": "",
                "result": None,
                "error": None,
                "provider_task_id": "",
                "result_url": "",
                "created_at": time.time(),
                "updated_at": time.time(),
            }
            self.db.execute(
                "INSERT INTO tasks VALUES (?,?,?,?,?,?)",
                (value["id"], scope, request.request_id, fingerprint, "queued", encode(value)),
            )
            self.db.execute("COMMIT")
            return value, True
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def task(self, identity):
        row = self.db.execute("SELECT value FROM tasks WHERE id=?", (identity,)).fetchone()
        if not row:
            raise CoreError("任务不存在", "not_found")
        return json.loads(row[0])

    def tasks(self, active=False):
        query = (
            "SELECT value FROM tasks WHERE status NOT IN ('completed','failed','cancelled','interrupted')"
            if active
            else "SELECT value FROM tasks ORDER BY rowid DESC LIMIT 500"
        )
        return [json.loads(row[0]) for row in self.db.execute(query)]

    def update(self, identity, **values):
        task = self.task(identity)
        previous_status = task["status"]
        task.update(values, updated_at=time.time())
        self.db.execute(
            "UPDATE tasks SET status=?,value=? WHERE id=?", (task["status"], encode(task), identity)
        )
        if task["status"] != previous_status:
            self.event(identity, "task_status", {"status": task["status"]})
        return task

    def event(self, identity, kind, value):
        self.db.execute(
            "INSERT INTO events(task_id,kind,value,created) VALUES (?,?,?,?)",
            (identity, kind, encode(value), time.time()),
        )

    def events(self, identity, after=0):
        return [
            {"seq": row[0], "kind": row[1], "value": json.loads(row[2]), "created_at": row[3]}
            for row in self.db.execute(
                "SELECT seq,kind,value,created FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT 500",
                (identity, after),
            )
        ]

    def recover(self):
        for task in self.tasks(active=True):
            if task["status"] in TERMINAL:
                continue
            if task["provider_task_id"] or task["result_url"]:
                self.update(task["id"], status="recoverable")
            elif task["status"] != "queued":
                self.update(
                    task["id"],
                    status="interrupted",
                    error={
                        "code": "execution_interrupted",
                        "message": "执行已中断，可检查结果后发起新任务",
                    },
                )

    def close(self):
        self.db.close()
