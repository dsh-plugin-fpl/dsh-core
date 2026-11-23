"""Account-owned imported history, retained independently of the old website."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel

from .identity_schema import Migration, apply, check
from .store import encode

MIGRATIONS = (
    Migration(
        "storage-history-v1",
        """
CREATE TABLE imported_records(dataset TEXT NOT NULL,id TEXT NOT NULL,source TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(dataset,id));
CREATE INDEX imported_record_source ON imported_records(source,dataset);
""",
    ),
)
PUBLIC = frozenset(
    {
        "llm_call_logs",
        "model_test_conversations",
        "model_test_messages",
        "model_inference_tasks",
        "model_test_generation_tasks",
        "model_test_transcription_tasks",
        "local_model_operations",
        "local_model_installations",
    }
)


class HistoryDataset(BaseModel):
    dataset: str
    count: int
    readable: bool


class HistoryInventory(BaseModel):
    items: list[HistoryDataset]


class StorageHistory:
    def __init__(self, root, *, initialize=False):
        self.path = Path(root) / "imported-history.sqlite3"
        if initialize:
            apply(self.path, "core-imported-history", MIGRATIONS)

    @contextmanager
    def database(self):
        check(self.path, "core-imported-history", MIGRATIONS)
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, dataset, identity, source, value):
        with self.database() as db:
            old = db.execute(
                "SELECT source,value FROM imported_records WHERE dataset=? AND id=?",
                (dataset, identity),
            ).fetchone()
            payload = encode(value)
            if old and (old[0] != source or old[1] != payload):
                raise ValueError("旧记录与导入回执不一致")
            db.execute(
                "INSERT INTO imported_records VALUES(?,?,?,?) ON CONFLICT(dataset,id) DO NOTHING",
                (dataset, identity, source, payload),
            )

    def inventory(self):
        with self.database() as db:
            return {
                "items": [
                    {"dataset": name, "count": count, "readable": name in PUBLIC}
                    for name, count in db.execute(
                        "SELECT dataset,count(*) FROM imported_records GROUP BY dataset ORDER BY dataset"
                    )
                ]
            }

    def records(self, dataset):
        with self.database() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT value FROM imported_records WHERE dataset=? ORDER BY id", (dataset,)
                )
            ]

    def get(self, dataset, identity):
        with self.database() as db:
            row = db.execute(
                "SELECT value FROM imported_records WHERE dataset=? AND id=?", (dataset, identity)
            ).fetchone()
            return json.loads(row[0]) if row else None


def register(app):
    from fastapi import HTTPException

    @app.get("/v1/import-history", response_model=HistoryInventory)
    async def inventory():
        return app.state.imported_history.inventory()

    @app.get("/v1/import-history/{dataset}")
    async def records(dataset: str, offset: int = 0, limit: int = 50):
        if dataset not in PUBLIC:
            raise HTTPException(403, "此数据集只通过所属能力的正式接口访问")
        if offset < 0 or not 1 <= limit <= 100:
            raise HTTPException(422, "分页参数无效")
        values = app.state.imported_history.records(dataset)
        return {"items": values[offset : offset + limit], "total": len(values)}
