"""Versioned configuration snapshots, including credentials only at the transfer boundary."""

import base64
import io
import shutil
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .contracts.catalog import McpCreateIn, McpUpdateIn, ToolApiCreateIn, ToolApiUpdateIn
from .contracts.errors import IntegrationNotFoundError
from .contracts.external_connections import ExternalConnectionInput, ExternalConnectionUpdate
from .dependencies import OWNER
from .storage.external_models import ExternalConnection
from .storage.models import McpIntegration, SkillIntegration, ToolApiIntegration

Kind = Literal["tool-apis", "mcp", "skills", "external-connections"]
MODELS = {
    "tool-apis": ToolApiIntegration,
    "mcp": McpIntegration,
    "skills": SkillIntegration,
    "external-connections": ExternalConnection,
}


class PortableIntegration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    kind: Kind
    id: str = Field(min_length=1, max_length=240)
    name: str = Field(min_length=1, max_length=120)
    revision: str = Field(max_length=128)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    config: dict[str, Any]
    archive_base64: str = Field(default="", max_length=28 * 1024 * 1024)


class MemoryUpload:
    def __init__(self, data):
        self.value = io.BytesIO(data)

    async def read(self, size=-1):
        return self.value.read(size)

    async def close(self):
        self.value.close()


class PortableCatalog:
    def __init__(self, manager):
        self.manager = manager

    def catalog(self, session, kind):
        return {
            "tool-apis": self.manager.tool_catalog,
            "mcp": self.manager.mcp_catalog,
            "skills": self.manager.skill_catalog,
            "external-connections": self.manager.external_catalog,
        }[kind](session)

    async def list(self):
        items = []
        async with self.manager.database.sessions() as session:
            for kind, model in MODELS.items():
                rows = (
                    await session.scalars(
                        select(model).where(model.owner_user_id == OWNER).order_by(model.name)
                    )
                ).all()
                for row in rows:
                    secrets = []
                    if kind == "tool-apis" and row.secret_ciphertext:
                        secrets = ["secret"]
                    if kind == "mcp":
                        secrets = (
                            (["bearer_token"] if row.bearer_ciphertext else [])
                            + ["headers." + key for key in row.header_keys]
                            + ["environment." + key for key in row.environment_keys]
                        )
                    if kind == "external-connections":
                        secrets = ["app_secret"] if row.secret_ciphertext else []
                    items.append(
                        {
                            "id": row.id,
                            "kind": kind,
                            "name": row.name,
                            "revision": str(row.revision),
                            "enabled": row.enabled,
                            "secret_fields": secrets,
                        }
                    )
        return items

    async def read(self, kind, identity):
        if kind not in MODELS:
            raise ValueError("集成类型无效")
        async with self.manager.database.sessions() as session:
            row = await session.get(MODELS[kind], identity)
            if row is None or row.owner_user_id != OWNER:
                raise IntegrationNotFoundError("集成不存在")
            cipher = self.manager.cipher
            if kind == "tool-apis":
                fields = (
                    "name",
                    "description",
                    "method",
                    "url",
                    "auth_kind",
                    "auth_header_name",
                    "parameters_schema",
                    "query_template",
                    "body_template",
                    "tags",
                    "sensitive_result",
                )
                config = {key: getattr(row, key) for key in fields}
                config["secret"] = (
                    cipher.decrypt(row.secret_ciphertext) if row.secret_ciphertext else ""
                )
            elif kind == "mcp":
                fields = (
                    "name",
                    "description",
                    "transport",
                    "url",
                    "command",
                    "arguments",
                    "cwd",
                    "timeout_ms",
                    "tags",
                    "sensitive_result",
                )
                config = {key: getattr(row, key) for key in fields}
                import json

                config.update(
                    bearer_token=cipher.decrypt(row.bearer_ciphertext)
                    if row.bearer_ciphertext
                    else "",
                    environment=json.loads(cipher.decrypt(row.environment_ciphertext))
                    if row.environment_ciphertext
                    else {},
                    headers=json.loads(cipher.decrypt(row.headers_ciphertext))
                    if row.headers_ciphertext
                    else {},
                )
            elif kind == "external-connections":
                config = {
                    "provider": row.provider,
                    "name": row.name,
                    "app_id": row.app_id,
                    "app_secret": cipher.decrypt(row.secret_ciphertext),
                }
            else:
                config = {
                    "name": row.name,
                    "description": row.description,
                    "tags": row.tags,
                    "default_version": row.default_version,
                }
            config["enabled"] = False
            archive = ""
            if kind == "skills":
                _, data = await self.catalog(session, kind).version_zip(row.id, row.default_version)
                archive = base64.b64encode(data).decode()
            return PortableIntegration(
                kind=kind,
                id=row.id,
                name=row.name,
                revision=str(row.revision),
                config=config,
                archive_base64=archive,
            )

    async def apply(self, item, *, target_id, expected_revision, create, receipt_id):
        """Catalog writes and receipt share one owned transaction; stage files remain reversible."""
        from .storage.transfer_receipts import TransferReceipt
        from .support.revisions import RevisionConflictError
        from .support.time import utc_now

        async with self.manager.database.sessions() as session:
            previous = await session.get(TransferReceipt, receipt_id)
            if previous:
                return previous.result
            session.defer_commit = True
            config = {**item.config, "name": item.name, "enabled": False}
            old_version = None
            placed = None
            try:
                catalog = self.catalog(session, item.kind)
                if create:
                    if item.kind == "tool-apis":
                        result = await catalog.create(
                            ToolApiCreateIn.model_validate(config), identity=target_id
                        )
                    elif item.kind == "mcp":
                        result = await catalog.create(
                            McpCreateIn.model_validate(config), identity=target_id
                        )
                    elif item.kind == "external-connections":
                        result = await catalog.save(
                            ExternalConnectionInput.model_validate(config), identity=target_id
                        )
                    else:
                        result = await catalog.create(
                            MemoryUpload(base64.b64decode(item.archive_base64, validate=True)),
                            enabled=False,
                            tags=config.get("tags", []),
                            identity=target_id,
                        )
                        placed = catalog.storage.final_path(result.id, result.latest_version)
                else:
                    current = await session.get(MODELS[item.kind], target_id)
                    if current is None or current.owner_user_id != OWNER:
                        raise IntegrationNotFoundError("更新目标不存在")
                    if str(current.revision) != str(expected_revision):
                        raise RevisionConflictError(
                            int(expected_revision),
                            current.revision,
                            {"id": target_id, "revision": current.revision},
                        )
                    config["expected_revision"] = current.revision
                    if item.kind == "tool-apis":
                        result = await catalog.update(
                            target_id,
                            ToolApiUpdateIn.model_validate({**config, "clear_secret": True}),
                        )
                    elif item.kind == "mcp":
                        result = await catalog.update(
                            target_id,
                            McpUpdateIn.model_validate(
                                {
                                    **config,
                                    "clear_environment": True,
                                    "clear_headers": True,
                                    "clear_bearer_token": True,
                                }
                            ),
                        )
                    elif item.kind == "external-connections":
                        result = await catalog.save(
                            ExternalConnectionUpdate.model_validate(config), target_id
                        )
                    else:
                        old_version = current.latest_version
                        result = await catalog.add_version(
                            target_id,
                            MemoryUpload(base64.b64decode(item.archive_base64, validate=True)),
                            expected_revision=current.revision,
                        )
                        # add_version preserves tags/enablement; imported copies stay disabled until explicitly enabled.
                        from .contracts.catalog import SkillUpdateIn

                        result = await catalog.update(
                            target_id,
                            SkillUpdateIn(
                                expected_revision=result.revision,
                                default_version=result.latest_version,
                                enabled=False,
                                tags=config.get("tags", []),
                            ),
                        )
                        if result.latest_version != old_version:
                            placed = catalog.storage.final_path(result.id, result.latest_version)
                value = {
                    "id": result.id,
                    "revision": str(result.revision),
                    "name": result.name,
                    "kind": item.kind,
                }
                session.add(TransferReceipt(id=receipt_id, result=value, created_at=utc_now()))
                session.defer_commit = False
                await session.commit()
                return value
            except BaseException:
                await session.rollback()
                # A lost commit confirmation is checked before a placed Skill directory can be removed.
                async with self.manager.database.sessions() as check:
                    if known := await check.get(TransferReceipt, receipt_id):
                        return known.result
                if placed:
                    shutil.rmtree(placed, ignore_errors=True)
                raise
