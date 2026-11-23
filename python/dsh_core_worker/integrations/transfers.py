"""Owner-confirmed, encrypted configuration copies with per-item durable receipts."""

import asyncio
import hashlib
import json
import time
from typing import Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..contracts import CoreError
from .portable import Kind, PortableCatalog, PortableIntegration
from .snapshots import archive_digest, rename_skill
from .support.revisions import RevisionConflictError


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind
    id: str = Field(min_length=1, max_length=240)
    target_id: str = Field(default="", max_length=240)


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Literal["from-dsh", "to-dsh"]
    bridge_id: str = Field(min_length=1, max_length=80)
    adapter_id: str = Field(min_length=1, max_length=80)
    items: list[Selection] = Field(min_length=1, max_length=50)


class Choice(BaseModel):
    key: str
    action: Literal["create", "update", "skip"]


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choices: list[Choice] = Field(min_length=1, max_length=50)


class IntegrationSummary(BaseModel):
    id: str
    kind: Kind
    name: str
    revision: str
    enabled: bool = False
    secret_fields: list[str] = Field(default_factory=list)


class SummaryList(BaseModel):
    items: list[IntegrationSummary]


class AdapterOut(BaseModel):
    bridge_id: str
    bridge_name: str
    id: str
    name: str
    kinds: list[Kind]
    can_import: bool
    can_export: bool


class AdapterList(BaseModel):
    items: list[AdapterOut]


class ImportResult(BaseModel):
    id: str
    kind: Kind
    name: str
    revision: str


class TransferRow(BaseModel):
    key: str
    kind: Kind
    name: str
    source_id: str
    source_revision: str
    target_id: str
    target_revision: str
    create_name: str
    action: Literal["create", "update", "skip"]
    changes: list[str]
    secret_fields: list[str]
    warnings: list[str]
    status: str
    applied_action: Literal["create", "update"] | None = None
    result: ImportResult | None = None
    message: str = ""


class TransferOut(BaseModel):
    id: str
    direction: Literal["from-dsh", "to-dsh"]
    bridge_id: str
    adapter_id: str
    created_at: float
    status: str
    rows: list[TransferRow]


def secret_fields(config):
    fields = []
    for key, value in config.items():
        if key in ("headers", "environment") and isinstance(value, dict):
            fields.extend(key + "." + k for k, v in value.items() if v)
        elif any(p in key.lower() for p in ("secret", "token", "api_key", "password")) and value:
            fields.append(key)
    return fields


class Transfers:
    def __init__(self, manager, bridges, access):
        self.manager, self.bridges, self.access = manager, bridges, access
        self.store = manager.runtime.store
        self.portable = PortableCatalog(manager)
        self.locks = {}

    def bridge(self, identity, adapter, direction=None):
        bridge = self.bridges.online.get(identity)
        if bridge is None:
            raise CoreError("DSH 未连接，请重新连接后操作", "bridge_disconnected")
        if not any(a["id"] == adapter for a in bridge.integration_adapters):
            raise CoreError("插件未提供此集成适配器", "adapter_unavailable")
        descriptor = next(a for a in bridge.integration_adapters if a["id"] == adapter)
        if direction and not descriptor["can_export" if direction == "from-dsh" else "can_import"]:
            raise CoreError("此适配器不支持该导入方向", "forbidden")
        return bridge

    def load(self, identity):
        row = self.store.config("integration-transfer:" + identity, None)
        if not row["value"]:
            raise CoreError("导入预览不存在", "not_found")
        return row

    def save(self, identity, value):
        row = self.store.config("integration-transfer:" + identity, {})
        self.store.save_config("integration-transfer:" + identity, value, row["revision"])

    def public(self, value):
        return {key: val for key, val in value.items() if key != "payload"}

    def sources(self):
        pairs = {p["id"]: p["name"] for p in self.access.bridges()}
        return [
            {
                "bridge_id": b.id,
                "bridge_name": pairs.get(b.id, "DSH"),
                "id": a["id"],
                "name": a["name"],
                "kinds": a["kinds"],
                "can_import": a["can_import"],
                "can_export": a["can_export"],
            }
            for b in self.bridges.online.values()
            for a in b.integration_adapters
        ]

    async def catalog(self, direction, bridge_id, adapter_id):
        bridge = self.bridge(bridge_id, adapter_id, direction)
        if direction == "from-dsh":
            rows = await bridge.call("integrations.list", {"adapter_id": adapter_id})
            if not isinstance(rows, list) or len(rows) > 1000:
                raise CoreError("插件集成目录无效")
            # The browser receives metadata only, even from third-party adapters.
            return [
                {
                    "id": str(row["id"])[:240],
                    "kind": row["kind"],
                    "name": str(row["name"])[:120],
                    "revision": str(row.get("revision", ""))[:128],
                    "enabled": bool(row.get("enabled")),
                    "secret_fields": [str(s)[:120] for s in row.get("secret_fields", [])][:100],
                }
                for row in rows
                if row.get("kind") in ("tool-apis", "mcp", "skills", "external-connections")
            ]
        return await self.portable.list()

    def map_key(self, direction, bridge, adapter, kind, identity):
        key = json.dumps([direction, bridge, adapter, kind, identity])
        return "integration-map:" + hashlib.sha256(key.encode()).hexdigest()

    async def read_source(self, direction, bridge, adapter, kind, identity):
        descriptor = next(a for a in bridge.integration_adapters if a["id"] == adapter)
        if kind not in descriptor["kinds"]:
            raise CoreError("适配器不支持此配置类型", "forbidden")
        if direction == "from-dsh":
            item = PortableIntegration.model_validate(
                await bridge.call(
                    "integrations.export", {"adapter_id": adapter, "kind": kind, "id": identity}
                )
            )
            if item.id != identity or item.kind != kind:
                raise CoreError("导出配置与所选条目不一致")
            return item
        return await self.portable.read(kind, identity)

    async def preview(self, body):
        bridge = self.bridge(body.bridge_id, body.adapter_id, body.direction)
        if body.direction == "from-dsh":
            targets = await self.portable.list()
        else:
            targets = await bridge.call("integrations.list", {"adapter_id": body.adapter_id})
        plan_id = str(uuid4())
        rows = []
        payload = []
        size = 0
        for index, selection in enumerate(body.items):
            item = await self.read_source(
                body.direction, bridge, body.adapter_id, selection.kind, selection.id
            )
            raw = item.model_dump()
            size += len(json.dumps(raw))
            if size > 64 * 1024 * 1024:
                raise CoreError("本次配置包过大，请分批导入")
            mapping = self.store.config(
                self.map_key(body.direction, body.bridge_id, body.adapter_id, item.kind, item.id),
                {},
            )["value"]
            target_id = selection.target_id or mapping.get("id", "")
            target = next(
                (t for t in targets if t["id"] == target_id and t["kind"] == item.kind), None
            )
            warnings = list(item.warnings)
            action = "update" if target else "create"
            if (
                target
                and mapping.get("revision")
                and str(target["revision"]) != mapping["revision"]
            ):
                action = "skip"
                warnings.append("目标副本在上次导入后已修改；请选择是否更新")
            if target:
                if body.direction == "from-dsh":
                    existing = await self.portable.read(item.kind, target["id"])
                else:
                    existing = PortableIntegration.model_validate(
                        await bridge.call(
                            "integrations.export",
                            {"adapter_id": body.adapter_id, "kind": item.kind, "id": target["id"]},
                        )
                    )
                fields = sorted(
                    k
                    for k in set(item.config) | set(existing.config)
                    if item.config.get(k) != existing.config.get(k)
                )
                if archive_digest(item.archive_base64) != archive_digest(existing.archive_base64):
                    fields.append("Skill 文件包")
            else:
                fields = sorted(item.config)
            # Creating a separate Core copy must not collide with a manually created entry of the same name.
            candidate = item.name
            names = {t["name"] for t in targets if t["kind"] == item.kind}
            number = 1
            if not target:
                while candidate in names:
                    number += 1
                    candidate = item.name[:100] + (
                        f"-import-{number}" if item.kind == "skills" else f"（导入 {number}）"
                    )
                if item.kind == "skills" and candidate != item.name:
                    item = rename_skill(item, candidate)
                    warnings.append("同名 Skill 将保存为 " + candidate + "，文件包名称同步更新")
                item = item.model_copy(
                    update={"name": candidate, "config": {**item.config, "name": candidate}}
                )
                targets.append(
                    {"kind": item.kind, "name": candidate, "id": "reserved-" + str(index)}
                )
            elif item.kind == "skills" and item.name != target["name"]:
                item = rename_skill(item, target["name"])
                warnings.append("保留目标 Skill 名称 " + target["name"])
            create_name = item.name
            if target:
                number = 1
                names = {t["name"] for t in targets if t["kind"] == item.kind}
                while create_name in names:
                    number += 1
                    create_name = item.name[:100] + (
                        f"-import-{number}" if item.kind == "skills" else f"（导入 {number}）"
                    )
                warnings.append("如选择新建副本，名称为 " + create_name)
            key = str(index)
            rows.append(
                {
                    "key": key,
                    "kind": item.kind,
                    "name": item.name,
                    "create_name": create_name,
                    "source_id": selection.id,
                    "source_revision": item.revision,
                    "target_id": target["id"] if target else "",
                    "target_revision": str(target["revision"]) if target else "",
                    "action": action,
                    "changes": fields,
                    "secret_fields": secret_fields(item.config),
                    "warnings": warnings,
                    "status": "pending",
                }
            )
            payload.append(item.model_dump())
        value = {
            "id": plan_id,
            "direction": body.direction,
            "bridge_id": body.bridge_id,
            "adapter_id": body.adapter_id,
            "created_at": time.time(),
            "status": "preview",
            "rows": rows,
            "payload": self.access.cipher.encrypt(json.dumps(payload).encode()).decode(),
        }
        self.save(plan_id, value)
        return self.public(value)

    def completed(self, value, row, result):
        row.update(status="completed", result=result, message="已保存为停用副本，可检查后启用")
        key = self.map_key(
            value["direction"],
            value["bridge_id"],
            value["adapter_id"],
            row["kind"],
            row["source_id"],
        )
        old = self.store.config(key, {})
        self.store.save_config(key, result, old["revision"])

    async def apply(self, identity, choices):
        async with self.locks.setdefault(identity, asyncio.Lock()):
            value = self.load(identity)["value"]
            if value["status"] == "completed":
                return self.public(value)
            if time.time() - value["created_at"] > 3600:
                raise CoreError("预览已失效，请重新预览")
            bridge = self.bridge(value["bridge_id"], value["adapter_id"], value["direction"])
            payload = json.loads(self.access.cipher.decrypt(value["payload"].encode()))
            selected = {c.key: c.action for c in choices}
            if len(selected) != len(choices) or set(selected) != {r["key"] for r in value["rows"]}:
                raise CoreError("请为每项配置选择导入方式")
            for row, raw in zip(value["rows"], payload, strict=True):
                if row["status"] in ("completed", "skipped"):
                    continue
                action = selected[row["key"]]
                if row.get("applied_action") and action != row["applied_action"]:
                    raise CoreError("已执行的项目不能更改导入方式，请核对原回执")
                if action == "skip":
                    row["status"] = "skipped"
                    self.save(identity, value)
                    continue
                if action == "update" and not row["target_id"]:
                    raise CoreError("没有可更新的目标，请选择新建副本")
                request_id = identity + "/" + row["key"]
                # A prior in-flight write is queried, never blindly submitted again.
                if row["status"] in ("applying", "unknown"):
                    if value["direction"] == "to-dsh":
                        receipt = await bridge.call(
                            "integrations.receipt",
                            {"adapter_id": value["adapter_id"], "request_id": request_id},
                        )
                        if receipt.get("status") == "completed":
                            self.completed(value, row, receipt["result"])
                            self.save(identity, value)
                            continue
                        row.update(status="unknown", message="目标端回执尚未确认，请稍后检查")
                        self.save(identity, value)
                        continue
                    from .storage.transfer_receipts import TransferReceipt

                    async with self.manager.database.sessions() as session:
                        receipt = await session.get(TransferReceipt, request_id)
                    if receipt:
                        self.completed(value, row, receipt.result)
                        self.save(identity, value)
                        continue
                item = PortableIntegration.model_validate(raw)
                try:
                    current = await self.read_source(
                        value["direction"],
                        bridge,
                        value["adapter_id"],
                        row["kind"],
                        row["source_id"],
                    )
                except Exception:  # noqa: BLE001 - source adapters may raise provider-specific errors
                    row.update(status="failed", message="无法读取源配置，请核对连接或重新预览")
                    self.save(identity, value)
                    continue
                if current.revision != row["source_revision"]:
                    row.update(status="failed", message="源配置已变化，请重新预览")
                    self.save(identity, value)
                    continue
                if action == "create" and item.name != row["create_name"]:
                    item = (
                        rename_skill(item, row["create_name"])
                        if item.kind == "skills"
                        else item.model_copy(
                            update={
                                "name": row["create_name"],
                                "config": {**item.config, "name": row["create_name"]},
                            }
                        )
                    )
                    raw = item.model_dump()
                target_id = (
                    row["target_id"]
                    if action == "update"
                    else str(uuid5(NAMESPACE_URL, request_id))
                )
                row.update(status="applying", applied_action=action)
                self.save(identity, value)
                try:
                    if value["direction"] == "from-dsh":
                        result = await self.portable.apply(
                            item,
                            target_id=target_id,
                            expected_revision=row["target_revision"],
                            create=action == "create",
                            receipt_id=request_id,
                        )
                    else:
                        result = await bridge.call(
                            "integrations.import",
                            {
                                "adapter_id": value["adapter_id"],
                                "item": raw,
                                "target_id": target_id,
                                "expected_revision": row["target_revision"],
                                "create": action == "create",
                                "request_id": request_id,
                            },
                        )
                    self.completed(value, row, result)
                except RevisionConflictError:
                    row.update(status="failed", message="目标配置已变化，请重新预览")
                except asyncio.CancelledError:
                    row.update(status="unknown", message="连接中断，先检查原回执")
                    self.save(identity, value)
                    raise
                except Exception as error:  # noqa: BLE001 - preserve uncertain adapter outcomes
                    uncertain = getattr(error, "code", "") in (
                        "bridge_disconnected",
                        "bridge_call_failed",
                    )
                    row.update(
                        status="unknown" if uncertain else "failed",
                        message="目标回执需要核对"
                        if uncertain
                        else "导入失败，请检查配置、鉴权和目标版本后重试",
                    )
                self.save(identity, value)
            value["status"] = (
                "completed"
                if all(r["status"] in ("completed", "skipped") for r in value["rows"])
                else "partial"
            )
            if value["status"] == "completed":
                value.pop("payload", None)
            self.save(identity, value)
            return self.public(value)


router = APIRouter(prefix="/v1/integrations", tags=["integration-transfers"])


@router.get("/dsh-adapters", response_model=AdapterList)
async def adapters(request: Request):
    return {"items": request.app.state.integration_transfers.sources()}


@router.get("/transfer-candidates", response_model=SummaryList)
async def candidates(
    request: Request, direction: Literal["from-dsh", "to-dsh"], bridge_id: str, adapter_id: str
):
    return {
        "items": await request.app.state.integration_transfers.catalog(
            direction, bridge_id, adapter_id
        )
    }


@router.post("/transfers/preview", response_model=TransferOut)
async def preview(request: Request, body: PreviewIn):
    return await request.app.state.integration_transfers.preview(body)


@router.post("/transfers/{identity}/apply", response_model=TransferOut)
async def apply(request: Request, identity: str, body: ApplyIn):
    return await request.app.state.integration_transfers.apply(identity, body.choices)


@router.get("/transfers/{identity}", response_model=TransferOut)
async def receipt(request: Request, identity: str):
    return request.app.state.integration_transfers.public(
        request.app.state.integration_transfers.load(identity)["value"]
    )
