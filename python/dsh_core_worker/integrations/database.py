"""Owned integration database with an explicit, checksummed initial migration."""

import hashlib
import os
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.schema import CreateIndex, CreateTable


class OwnedSession(AsyncSession):
    defer_commit = False

    async def commit(self):
        if self.defer_commit:
            await self.flush()
        else:
            await super().commit()


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.path = self.root / "integrations.sqlite3"
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///" + str(self.path), connect_args={"timeout": 30}
        )

        @event.listens_for(self.engine.sync_engine, "connect")
        def setup(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=OwnedSession)

    async def migrate(self):
        from .storage.external_models import ExternalConnection
        from .storage.models import (
            IntegrationCheck,
            McpDiscoveredTool,
            McpIntegration,
            SkillIntegration,
            SkillVersion,
            ToolApiIntegration,
        )
        from .storage.transfer_receipts import TransferReceipt

        # This list is deliberately owned and explicit, never discovered across application metadata.
        tables = [
            m.__table__
            for m in (
                ToolApiIntegration,
                McpIntegration,
                McpDiscoveredTool,
                SkillIntegration,
                SkillVersion,
                IntegrationCheck,
                ExternalConnection,
                TransferReceipt,
            )
        ]
        sql = []
        for table in tables:
            sql.append(str(CreateTable(table).compile(dialect=dialect())))
            sql.extend(
                str(CreateIndex(i).compile(dialect=dialect()))
                for i in sorted(table.indexes, key=lambda i: i.name)
            )
        checksum = hashlib.sha256("\n".join(sql).encode()).hexdigest()
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS integration_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)"
                )
            )
            rows = (
                await connection.execute(
                    text("SELECT version,checksum FROM integration_migrations")
                )
            ).all()
            if rows and rows != [(1, checksum)]:
                raise RuntimeError("Integration migration ledger mismatch")
            if not rows:
                for statement in sql:
                    await connection.execute(text(statement))
                await connection.execute(
                    text("INSERT INTO integration_migrations VALUES (1,:checksum)"),
                    {"checksum": checksum},
                )
        os.chmod(self.path, 0o600)

    async def close(self):
        await self.engine.dispose()
