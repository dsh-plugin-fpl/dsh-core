"""Append-only, checksummed schema for owned external synchronization receipts."""

import hashlib

from sqlalchemy import text
from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from .records import ExternalClosure, ExternalExecution, ExternalStepReceipt


async def migrate(database):
    statements = []
    for model in (ExternalExecution, ExternalStepReceipt, ExternalClosure):
        table = model.__table__
        statements.append(str(CreateTable(table).compile(dialect=dialect())))
        statements.extend(
            str(CreateIndex(index).compile(dialect=dialect()))
            for index in sorted(table.indexes, key=lambda i: i.name)
        )
    checksum = hashlib.sha256("\n".join(statements).encode()).hexdigest()
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS external_sync_migrations (version INTEGER PRIMARY KEY,checksum TEXT NOT NULL)"
            )
        )
        rows = (
            await connection.execute(
                text("SELECT version,checksum FROM external_sync_migrations ORDER BY version")
            )
        ).all()
        if rows and rows != [(1, checksum)]:
            raise RuntimeError("External synchronization migration ledger mismatch")
        if not rows:
            for statement in statements:
                await connection.execute(text(statement))
            await connection.execute(
                text("INSERT INTO external_sync_migrations VALUES(1,:checksum)"),
                {"checksum": checksum},
            )
