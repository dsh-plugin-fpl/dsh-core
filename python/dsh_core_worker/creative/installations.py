"""Fixed website releases installed as disabled, account-owned local copies."""

import base64
import hashlib
import json
import shutil
import time
import zipfile
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import HTTPException
from sqlalchemy import select

from ..integrations.contracts.catalog import McpCreateIn, ToolApiCreateIn
from ..integrations.portable import MemoryUpload, PortableCatalog, PortableIntegration
from ..integrations.snapshots import files, rename_skill
from ..integrations.storage.transfer_receipts import TransferReceipt
from ..integrations.support.time import utc_now

KINDS = {"tool_api": "tool-apis", "mcp": "mcp", "skill": "skills"}
PUBLIC_FIELDS = {
    "tool_api": (
        "method",
        "url",
        "auth_kind",
        "auth_header_name",
        "parameters_schema",
        "query_template",
        "body_template",
    ),
    "mcp": ("transport", "url", "command_hint"),
    "skill": ("name", "description", "content_sha256", "file_count", "expanded_bytes"),
}


def public_manifest(kind, manifest):
    if kind not in PUBLIC_FIELDS:
        raise HTTPException(422, "不支持的能力类型")
    return {key: manifest[key] for key in PUBLIC_FIELDS[kind] if key in manifest}


class Installations:
    def __init__(self, website, manager):
        self.website, self.manager = website, manager
        self.portable = PortableCatalog(manager)

    def config(self, key, value=None):
        store = self.manager.runtime.store
        row = store.config("creative-preview:" + key, None)
        if value is not None:
            store.save_config("creative-preview:" + key, value, row["revision"])
        return row["value"]

    async def package(self, state, release_id):
        raw, _ = await self.website.send(
            state, "GET", f"/v1/creative-workshop/releases/{release_id}/package"
        )
        try:
            package = json.loads(raw)
            if package["schema_version"] != 1 or package["release_id"] != release_id:
                raise ValueError()
            parts = package["parts"]
            if not 1 <= len(parts) <= 30 or len({p["release"]["id"] for p in parts}) != len(parts):
                raise ValueError()
            for part in parts:
                release = part["release"]
                if release["kind"] not in KINDS or not release["installable"]:
                    raise ValueError()
                if release["kind"] == "skill":
                    data = base64.b64decode(part["archive_base64"], validate=True)
                    if hashlib.sha256(data).hexdigest() != part["archive_sha256"]:
                        raise ValueError()
                    files(part["archive_base64"])
            return package
        except (ValueError, KeyError, TypeError, zipfile.BadZipFile):
            raise HTTPException(422, "网站安装包格式或摘要无效") from None

    def fingerprint(self, package):
        return hashlib.sha256(
            json.dumps(package, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    async def receipts(self):
        async with self.manager.database.sessions() as session:
            return [
                row.result
                for row in (
                    await session.scalars(
                        select(TransferReceipt).where(TransferReceipt.id.startswith("creative:"))
                    )
                ).all()
            ]

    async def known_resources(self, state):
        targets = {t["id"] for t in await self.portable.list()}
        known = {}
        for receipt in await self.receipts():
            if receipt["site"] != state["site"]:
                continue
            for resource in receipt["resources"]:
                if resource["state"] != "pending_setup" and resource["id"] not in targets:
                    continue
                previous = known.get(resource["release_id"])
                if previous is None or resource["state"] == "installed":
                    known[resource["release_id"]] = resource
        return known

    async def preview(self, state, body):
        package = await self.package(state, body["release_id"])
        targets = await self.portable.list()
        previous = await self.receipts()
        known = await self.known_resources(state)
        parts = []
        for part in package["parts"]:
            release = part["release"]
            name = body.get("names", {}).get(
                release["id"],
                release["manifest"].get("name") if release["kind"] == "skill" else release["title"],
            )
            if not isinstance(name, str) or not name.strip() or len(name) > 120:
                raise HTTPException(422, "目标名称无效")
            name = name.strip()
            installed = known.get(release["id"])
            conflict = any(
                t["kind"] == KINDS[release["kind"]] and t["name"] == name for t in targets
            )
            conflict |= any(
                r["name"] == name and r["kind"] == release["kind"] and r["state"] == "pending_setup"
                for receipt in previous
                for r in receipt["resources"]
            )
            conflict |= any(
                p["kind"] == release["kind"] and p["target_name"] == name for p in parts
            )
            parts.append(
                {
                    "release_id": release["id"],
                    "kind": release["kind"],
                    "title": release["title"],
                    "target_name": name,
                    "conflict": conflict and installed is None,
                    "already_installed": installed is not None,
                    "pending_setup": release["kind"] == "mcp"
                    and release["manifest"].get("transport") == "stdio",
                }
            )
        identity = str(uuid4())
        value = {
            "id": identity,
            "site": state["site"],
            "account": state["account"]["id"],
            "created": time.time(),
            "release_id": body["release_id"],
            "parts": parts,
            "package": self.website.access.cipher.encrypt(json.dumps(package).encode()).decode(),
        }
        self.config(identity, value)
        return {
            "id": identity,
            "release_id": body["release_id"],
            "parts": parts,
            "installable": not any(p["conflict"] for p in parts),
        }

    async def receipt(self, identity):
        async with self.manager.database.sessions() as session:
            row = await session.get(TransferReceipt, "creative:" + identity)
            if row is None:
                raise HTTPException(404, "安装回执不存在")
            return row.result

    async def apply(self, state, identity):
        try:
            return await self.receipt(identity)
        except HTTPException as error:
            if error.status_code != 404:
                raise
        plan = self.config(identity)
        if not plan or time.time() - plan["created"] > 3600:
            raise HTTPException(409, "预览已失效，请重新检查依赖")
        if (plan["site"], plan["account"]) != (state["site"], state["account"]["id"]):
            raise HTTPException(409, "网站账号已切换，请重新预览")
        package = await self.package(state, plan["release_id"])
        expected = json.loads(self.website.access.cipher.decrypt(plan["package"].encode()))
        if self.fingerprint(package) != self.fingerprint(expected):
            raise HTTPException(409, "公开版本发生变化，请重新预览")
        if any(p["conflict"] for p in plan["parts"]):
            raise HTTPException(409, "请解决名称冲突后重新预览")
        prior = await self.known_resources(state)
        value = {
            "id": identity,
            "release_id": plan["release_id"],
            "site": state["site"],
            "resources": [],
            "already_installed": all(p["release_id"] in prior for p in plan["parts"]),
        }
        placed = []
        async with self.manager.database.sessions() as session:
            session.defer_commit = True
            try:
                for part, choice in zip(package["parts"], plan["parts"], strict=True):
                    release = part["release"]
                    if release["id"] in prior:
                        value["resources"].append(prior[release["id"]])
                        continue
                    resource = {
                        "release_id": release["id"],
                        "kind": release["kind"],
                        "name": choice["target_name"],
                        "state": "pending_setup" if choice["pending_setup"] else "installed",
                        "id": str(uuid5(NAMESPACE_URL, state["site"] + release["id"])),
                    }
                    if choice["pending_setup"]:
                        resource["description"] = release["summary"]
                        resource["command_hint"] = release["manifest"].get("command_hint", "")
                    else:
                        kind = release["kind"]
                        config = {
                            **public_manifest(kind, release["manifest"]),
                            "name": resource["name"],
                            "description": release["summary"],
                            "enabled": False,
                            "tags": release["tags"],
                        }
                        if kind == "tool_api":
                            await self.manager.tool_catalog(session).create(
                                ToolApiCreateIn.model_validate(config), identity=resource["id"]
                            )
                        elif kind == "mcp":
                            await self.manager.mcp_catalog(session).create(
                                McpCreateIn.model_validate(config), identity=resource["id"]
                            )
                        else:
                            item = rename_skill(
                                PortableIntegration(
                                    kind="skills",
                                    id=release["id"],
                                    name=resource["name"],
                                    revision=str(release["version"]),
                                    config={},
                                    archive_base64=part["archive_base64"],
                                ),
                                resource["name"],
                            )
                            catalog = self.manager.skill_catalog(session)
                            result = await catalog.create(
                                MemoryUpload(base64.b64decode(item.archive_base64)),
                                enabled=False,
                                tags=release["tags"],
                                identity=resource["id"],
                            )
                            placed.append(
                                catalog.storage.final_path(result.id, result.latest_version)
                            )
                    value["resources"].append(resource)
                session.add(
                    TransferReceipt(id="creative:" + identity, result=value, created_at=utc_now())
                )
                session.defer_commit = False
                await session.commit()
                return value
            except BaseException:
                await session.rollback()
                try:
                    return await self.receipt(identity)
                except HTTPException as error:
                    if error.status_code != 404:
                        raise
                for path in placed:
                    shutil.rmtree(path, ignore_errors=True)
                raise

    async def configure_stdio(self, identity, body):
        value = await self.receipt(identity)
        resource = next(
            (
                r
                for r in value["resources"]
                if r["release_id"] == body["release_id"] and r["kind"] == "mcp"
            ),
            None,
        )
        if resource is None:
            raise HTTPException(404, "待配置 MCP 不存在")
        if resource["state"] != "pending_setup":
            return value
        config = McpCreateIn(
            name=resource["name"],
            description=resource.get("description", ""),
            transport="stdio",
            command=body["command"],
            arguments=body.get("arguments", []),
            environment=body.get("environment", {}),
            enabled=False,
        )
        async with self.manager.database.sessions() as session:
            session.defer_commit = True
            from ..integrations.storage.models import McpIntegration

            if await session.get(McpIntegration, resource["id"]) is None:
                await self.manager.mcp_catalog(session).create(config, identity=resource["id"])
            resource["state"] = "installed"
            row = await session.get(TransferReceipt, "creative:" + identity)
            row.result = value
            session.defer_commit = False
            await session.commit()
        return value
