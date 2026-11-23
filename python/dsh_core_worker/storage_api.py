"""Authenticated machine-owner storage endpoints; payload schemas are provider-owned."""

import asyncio
from pathlib import Path
from uuid import UUID

from fastapi import Request
from pydantic import BaseModel, Field

from .contracts import CoreError
from .storage_manager import StorageManager


class StoragePlanInput(BaseModel):
    target_root: str = Field(min_length=1, max_length=4096)
    expected_revision: int = Field(ge=0)
    include_legacy: bool = False


class StorageMigrationInput(BaseModel):
    plan_id: UUID


class StorageParticipant(BaseModel):
    id: str
    root: str
    files: int
    bytes: int


class StorageJob(BaseModel):
    id: str
    status: str
    phase: str
    message: str
    created_at: float


class StorageStatus(BaseModel):
    configured: bool
    revision: int
    root: str
    core_root: str
    participants: list[StorageParticipant]
    jobs: list[StorageJob]
    legacy_source_configured: bool
    legacy_imported: bool


class StoragePlan(BaseModel):
    id: str
    root: str
    required_bytes: int
    available_bytes: int
    entries: list[StorageParticipant]
    legacy: dict | None


def install(app, root, accounts, current):
    manager = StorageManager(Path(__file__).resolve().parents[2], root)

    def owner(request):
        session = current(request, admin=True)
        if session["account_id"] != accounts.initial["id"]:
            raise CoreError("只有本机所有者可以修改机器存储", "forbidden")
        return session

    async def call(function, *args):
        try:
            return await asyncio.to_thread(function, *args)
        except (ValueError, OSError) as exc:
            raise CoreError(str(exc), "storage_unavailable") from None

    @app.get("/v1/storage", response_model=StorageStatus)
    async def status(request: Request):
        owner(request)
        return await call(manager.status)

    @app.post("/v1/storage/plans", response_model=StoragePlan)
    async def plan(request: Request, body: StoragePlanInput):
        owner(request)
        return await call(
            manager.plan, body.target_root, body.expected_revision, body.include_legacy
        )

    @app.post("/v1/storage/migrations", status_code=202, response_model=StorageJob)
    async def migrate(request: Request, body: StorageMigrationInput):
        owner(request)
        return await call(manager.start, str(body.plan_id))

    @app.get("/v1/storage/migrations/{identity}", response_model=StorageJob)
    async def job(identity: UUID, request: Request):
        owner(request)
        return await call(manager.job, str(identity))

    @app.post(
        "/v1/storage/migrations/{identity}/resume", status_code=202, response_model=StorageJob
    )
    async def resume(identity: UUID, request: Request):
        owner(request)
        return await call(manager.start, str(identity), True)

    @app.post(
        "/v1/storage/migrations/{identity}/cancel", status_code=202, response_model=StorageJob
    )
    async def cancel(identity: UUID, request: Request):
        owner(request)
        return await call(manager.cancel, str(identity))
