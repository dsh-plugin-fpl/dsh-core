"""GitHub resource snapshots and transactional, disabled local integration copies."""

import asyncio
import base64
import hashlib
import io
import json
import re
import shutil
import time
import zipfile
from pathlib import PurePosixPath
from uuid import NAMESPACE_URL, uuid5

import yaml
from fastapi import HTTPException
from sqlalchemy import select

from ..integrations.clients.skill_archive import SkillArchiveStager
from ..integrations.contracts.catalog import McpCreateIn, ToolApiCreateIn
from ..integrations.portable import MemoryUpload, PortableCatalog, PortableIntegration
from ..integrations.snapshots import rename_skill
from ..integrations.storage.transfer_receipts import TransferReceipt
from ..integrations.support.time import utc_now
from .github import GitHub, parse_url, safe_path

DEFAULT_SOURCE = {
    "id": "official",
    "name": "FPL 官方资源",
    "url": "https://github.com/EmiyaredA/fpl-resources",
    "ref": "",
}
TYPES = {"skill", "integration", "template", "workflow", "preset", "asset", "bundle"}
KINDS = {"skill": "skills", "tool_api": "tool-apis", "mcp": "mcp"}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def join(base, path):
    safe_path(path)
    return base + "/" + path if base else path


class ResourceLibrary:
    def __init__(self, manager):
        self.manager = manager
        self.github = GitHub()
        self.lock = asyncio.Lock()
        self.downloads = asyncio.Semaphore(6)

    def config(self, key, initial=None, *, save=False):
        store = self.manager.runtime.store
        row = store.config("resource-library:" + key, initial)
        if save:
            store.save_config("resource-library:" + key, initial, row["revision"])
        return row["value"] if row["revision"] else initial

    def sources(self):
        return self.config("sources", [DEFAULT_SOURCE])

    def source(self, identity):
        source = next((s for s in self.sources() if s["id"] == identity), None)
        if source is None:
            raise HTTPException(404, "资源来源不存在")
        return source

    async def add_source(self, url, ref="", name=""):
        locator = parse_url(url, ref)
        identity = (
            DEFAULT_SOURCE["id"]
            if locator == parse_url(DEFAULT_SOURCE["url"], DEFAULT_SOURCE["ref"])
            else digest(locator)[:24]
        )
        async with self.lock:
            for source in self.sources():
                if parse_url(source["url"], source["ref"]) == locator:
                    return source
            source = {
                "id": identity,
                "name": name.strip() or locator["repository"],
                "url": url.strip(),
                "ref": ref,
            }
            # Do not persist a source until it can be resolved and parsed.
            await self.refresh_source(source)
            self.config("sources", [*self.sources(), source], save=True)
            return source

    async def remove_source(self, identity):
        async with self.lock:
            self.source(identity)
            self.config("sources", [s for s in self.sources() if s["id"] != identity], save=True)
        return {"removed": True}

    async def file(self, snapshot, path, limit=64 * 1024 * 1024):
        # Cache only verified immutable content; paths on disk are content-addressed.
        row = snapshot["tree"].get(path, {})
        blob = row.get("sha", "")
        if len(blob) != 40 or any(c not in "0123456789abcdef" for c in blob):
            raise ValueError("资源文件不存在")
        cache = self.manager.root / "resource-cache" / blob
        if cache.is_file():
            data = cache.read_bytes()
            if (
                len(data) <= limit
                and hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                == blob
            ):
                # Check path and file mode even for a previously downloaded blob.
                safe_path(path)
                if row.get("mode") in {"100644", "100755"} and row.get("type") == "blob":
                    return data
        async with self.downloads:
            data = await self.github.file(snapshot, path, limit)
        cache.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = cache.with_suffix("." + digest(str(time.time_ns()))[:10])
        try:
            temporary.write_bytes(data)
            temporary.replace(cache)
        finally:
            temporary.unlink(missing_ok=True)
        return data

    async def refresh_source(self, source):
        snapshot = await self.github.snapshot(parse_url(source["url"], source["ref"]))
        old = self.config(f"snapshot:{source['id']}:{snapshot['revision']}")
        if old:
            snapshot = old
        else:
            prefix = snapshot["path"] + "/" if snapshot["path"] else ""
            descriptor = prefix + "source.json"
            if descriptor in snapshot["tree"]:
                meta = json.loads(await self.file(snapshot, descriptor, 256 * 1024))
                if not isinstance(meta, dict) or not isinstance(meta.get("source_id"), str):
                    raise ValueError("source.json 必须声明文本 source_id")
                if meta.get("schema_version") not in {"0.1-draft", 1, "1"}:
                    raise ValueError("资源来源清单版本不支持")
                snapshot["source_id"] = meta["source_id"]
            else:
                snapshot["source_id"] = snapshot["repository"]
            manifests = sorted(
                path
                for path in snapshot["tree"]
                if path.startswith(prefix)
                and path.rsplit("/", 1)[-1] == "resource.yaml"
                and not any(
                    p in {"docs", "tests", "dashboard", "tooling", "dist"}
                    for p in PurePosixPath(path[len(prefix) :]).parts
                )
            )
            # Ordinary repositories can expose several standalone Skill directories.
            candidates = [(p, True) for p in manifests]
            manifest_dirs = {p.rsplit("/", 1)[0] if "/" in p else "" for p in manifests}
            for path in sorted(snapshot["tree"]):
                if path.startswith(prefix) and path.rsplit("/", 1)[-1] == "SKILL.md":
                    base = path.rsplit("/", 1)[0] if "/" in path else ""
                    if base not in manifest_dirs and not any(
                        base.startswith(p + "/") for p in manifest_dirs
                    ):
                        candidates.append((path, False))
            if len(candidates) > 300:
                raise ValueError("资源数量超过 300 项限制，请使用较小的资源来源")
            results = await asyncio.gather(
                *(self.read_item(snapshot, p, manifest) for p, manifest in candidates),
                return_exceptions=True,
            )
            items, errors = [], []
            for (path, _), result in zip(candidates, results, strict=True):
                if isinstance(result, BaseException):
                    # Network errors must not turn an incomplete refresh into a successful one.
                    if isinstance(result, HTTPException):
                        raise result
                    errors.append({"path": path, "message": str(result)})
                else:
                    if any(i["id"] == result["id"] for i in items):
                        errors.append({"path": path, "message": "资源 ID 重复"})
                    else:
                        items.append(result)
            snapshot.update(items=items, errors=errors)
            if not items:
                raise ValueError(
                    "没有找到有效的 resource.yaml 或 SKILL.md"
                    + ("：" + errors[0]["message"] if errors else "")
                )
            self.config(f"snapshot:{source['id']}:{snapshot['revision']}", snapshot, save=True)
        self.config("current:" + source["id"], snapshot["revision"], save=True)
        return snapshot

    async def read_item(self, snapshot, path, manifest):
        base = path.rsplit("/", 1)[0] if "/" in path else ""
        raw = await self.file(snapshot, path, 256 * 1024)
        if manifest:
            item = yaml.safe_load(raw)
            if not isinstance(item, dict):
                raise ValueError("resource.yaml 必须是对象")
            for field in ["id", "name", "version", "description", "license", "format", "entry"]:
                if not isinstance(item.get(field), str) or not item[field].strip():
                    raise ValueError("资源清单缺少 " + field)
            if not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", item["id"]):
                raise ValueError("资源 id 格式无效")
            if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-z0-9.-]+)?", item["version"]):
                raise ValueError("资源 version 必须是固定语义版本")
            if item.get("type") not in TYPES or item.get("status") not in {"draft", "published"}:
                raise ValueError("资源类型或状态无效")
            if item["status"] == "published" and item["license"] == "LicenseRef-Unknown":
                raise ValueError("许可未知的内容不能标为已发布")
            paths = item.get("files")
            if not isinstance(paths, list) or not paths or item["entry"] not in paths:
                raise ValueError("资源入口必须在 files 中")
        else:
            name, description = SkillArchiveStager._metadata(raw)
            roots = {
                p.rsplit("/", 1)[0]
                for p in snapshot["tree"]
                if p.endswith("/SKILL.md") and p != path
            }
            paths = []
            for file, row in snapshot["tree"].items():
                if (base and not file.startswith(base + "/")) or (
                    not base and file.startswith(".git/")
                ):
                    continue
                if any(
                    file.startswith(p + "/") and (not base or p.startswith(base + "/"))
                    for p in roots
                ):
                    continue
                relative = file[len(base) + 1 :] if base else file
                if row.get("mode") == "120000":
                    raise ValueError("Skill 目录含符号链接")
                if row.get("type") == "blob":
                    safe_path(relative)
                    paths.append(relative)
            item = {
                "id": base or name,
                "name": name,
                "description": description,
                "version": snapshot["revision"],
                "type": "skill",
                "license": "LicenseRef-Unknown",
                "status": "draft",
                "format": "agent-skill",
                "entry": "SKILL.md",
            }
        if len(paths) > 1000 or len(paths) != len(set(paths)):
            raise ValueError("资源文件数量超限或有重复")
        for field in ["tags", "targets"]:
            if not isinstance(item.get(field, []), list) or any(
                not isinstance(v, str) for v in item.get(field, [])
            ):
                raise ValueError(field + " 必须是文本列表")
        if not isinstance(item.get("origin", ""), str):
            raise TypeError("origin 必须是文本")
        entries = []
        for relative in sorted(paths):
            full = join(base, relative)
            row = snapshot["tree"].get(full)
            if not row or row.get("mode") not in {"100644", "100755"}:
                raise ValueError("资源引用的文件缺失或不是普通文件：" + relative)
            entries.append({"path": relative, "size": row.get("size", 0), "git_sha": row["sha"]})
        previews = item.get("previews", [])
        if not isinstance(previews, list) or len(previews) > 20:
            raise ValueError("资源预览清单无效")
        for preview in previews:
            if not isinstance(preview, dict) or not isinstance(preview.get("label", ""), str):
                raise TypeError("资源预览必须包含文本字段")
            if preview.get("kind") not in {"image", "pdf", "video", "audio"}:
                raise ValueError("预览类型不支持")
            full = join(base, preview["path"])
            if snapshot["tree"].get(full, {}).get("mode") not in {"100644", "100755"}:
                raise ValueError("预览文件缺失或不是普通文件")
        dependencies = item.get("dependencies", [])
        if not isinstance(dependencies, list) or len(dependencies) > 30:
            raise ValueError("资源依赖清单无效")
        for dependency in dependencies:
            if not isinstance(dependency.get("id"), str) or not isinstance(
                dependency.get("version"), str
            ):
                raise TypeError("依赖必须指定 id 和固定 version")
        return {
            key: item.get(key, [] if key in {"tags", "targets"} else "")
            for key in [
                "id",
                "name",
                "description",
                "version",
                "type",
                "license",
                "status",
                "format",
                "tags",
                "targets",
                "origin",
            ]
        } | {
            "key": digest([base, item["id"]])[:24],
            "base": base,
            "entry": item["entry"],
            "files": entries,
            "previews": [{k: p[k] for k in ["path", "kind", "label"] if k in p} for p in previews],
            "dependencies": [{"id": d["id"], "version": d["version"]} for d in dependencies],
            "digest": digest([entries, snapshot["tree"][path]["sha"]]),
            "installable": item["type"] in {"skill", "integration"},
            "preview_content_sha256": item.get("preview_content_sha256", ""),
        }

    def snapshot(self, source_id, revision=""):
        self.source(source_id)
        revision = revision or self.config("current:" + source_id, "")
        snapshot = self.config(f"snapshot:{source_id}:{revision}")
        if snapshot is None:
            raise HTTPException(404, "资源来源尚未刷新或此版本没有缓存")
        return snapshot

    def item(self, source_id, revision, key):
        snapshot = self.snapshot(source_id, revision)
        item = next((i for i in snapshot["items"] if i["key"] == key), None)
        if item is None:
            raise HTTPException(404, "资源不存在")
        return snapshot, item

    async def catalog(self, source_id, refresh=False):
        source = self.source(source_id)
        snapshot = None
        error = None
        try:
            snapshot = self.snapshot(source_id)
        except HTTPException:
            pass
        if refresh or snapshot is None:
            try:
                async with self.lock:
                    snapshot = await self.refresh_source(source)
            except (HTTPException, ValueError, KeyError) as cause:
                if snapshot is None:
                    raise
                error = cause.detail if isinstance(cause, HTTPException) else str(cause)
        receipts = await self.receipts()
        current_ids = {row["id"] for row in await PortableCatalog(self.manager).list()}
        installed = [
            resource
            for receipt in receipts
            for resource in receipt["resources"]
            if receipt["source_id"] == source_id and resource["integration_id"] in current_ids
        ]
        items = [
            {
                **item,
                "installed": any(
                    r["resource_id"] == item["id"] and r["digest"] == item["digest"]
                    for r in installed
                ),
                "update_available": any(
                    r["resource_id"] == item["id"] and r["digest"] != item["digest"]
                    for r in installed
                ),
            }
            for item in snapshot["items"]
        ]
        return {
            "source": source,
            "revision": snapshot["revision"],
            "items": items,
            "errors": snapshot["errors"],
            "refresh_error": error,
        }

    async def detail(self, source_id, revision, key):
        snapshot, item = self.item(source_id, revision, key)
        text = ""
        if PurePosixPath(item["entry"]).suffix.lower() in {".md", ".txt", ".json", ".yaml", ".yml"}:
            text = (
                await self.file(snapshot, join(item["base"], item["entry"]), 256 * 1024)
            ).decode("utf-8", errors="replace")
        parts = self.dependencies(snapshot, item)
        preview_status = "missing"
        if item["previews"]:
            preview_status = "unverified"
            if (
                item["preview_content_sha256"]
                and sum(f["size"] for f in item["files"]) <= 100 * 1024 * 1024
            ):
                entries = []
                for entry in item["files"]:
                    data = await self.file(snapshot, join(item["base"], entry["path"]))
                    entries.append(
                        {
                            "path": entry["path"],
                            "size": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                        }
                    )
                actual = hashlib.sha256(
                    json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode()
                ).hexdigest()
                preview_status = "current" if actual == item["preview_content_sha256"] else "stale"
        return {
            **item,
            "revision": snapshot["revision"],
            "source_id": source_id,
            "content": text,
            "dependencies_resolved": parts,
            "preview_status": preview_status,
            "source_url": f"https://github.com/{snapshot['repository']}/tree/{snapshot['revision']}/{quote_path(item['base'])}",
        }

    def dependencies(self, snapshot, item):
        ordered, visiting, visited = [], set(), set()

        def visit(current):
            if current["key"] in visiting:
                raise ValueError("资源依赖存在循环")
            if current["key"] in visited:
                return
            visiting.add(current["key"])
            for dep in current["dependencies"]:
                match = next(
                    (
                        i
                        for i in snapshot["items"]
                        if i["id"] == dep["id"] and i["version"] == dep["version"]
                    ),
                    None,
                )
                if match is None:
                    raise ValueError("缺少固定版本依赖：" + dep["id"])
                visit(match)
            visiting.remove(current["key"])
            visited.add(current["key"])
            ordered.append(current)
            if len(ordered) > 30:
                raise ValueError("导入依赖超过 30 项限制")

        visit(item)
        return ordered

    async def receipts(self):
        async with self.manager.database.sessions() as session:
            rows = await session.scalars(
                select(TransferReceipt).where(TransferReceipt.id.startswith("resource-library:"))
            )
            return [row.result for row in rows.all()]

    async def prepare(self, snapshot, item, name, overrides):
        if not item["installable"]:
            raise ValueError("此资源类型暂不支持导入：" + item["type"])
        total = sum(f["size"] for f in item["files"])
        if total > 100 * 1024 * 1024:
            raise HTTPException(413, "资源展开后超过 100 MiB 限制")
        contents = {}
        actual_total = 0
        # Bound simultaneous memory and requests, rather than gathering every file.
        for entry in item["files"]:
            data = await self.file(snapshot, join(item["base"], entry["path"]))
            actual_total += len(data)
            if actual_total > 100 * 1024 * 1024:
                raise HTTPException(413, "资源展开后超过 100 MiB 限制")
            contents[entry["path"]] = data
        if item["type"] == "skill":
            if "SKILL.md" not in contents:
                raise ValueError("Skill 的 files 必须包含 SKILL.md")
            original_name, _ = SkillArchiveStager._metadata(contents["SKILL.md"])
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                for path, data in sorted(contents.items()):
                    info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, data)
            raw = output.getvalue()
            if len(raw) > 20 * 1024 * 1024:
                raise HTTPException(413, "Skill ZIP 超过 20 MiB 限制")
            target = name or original_name
            item_model = rename_skill(
                PortableIntegration(
                    kind="skills",
                    id=item["key"],
                    name=target,
                    revision=item["version"],
                    config={},
                    archive_base64=base64.b64encode(raw).decode(),
                ),
                target,
            )
            return "skill", target, base64.b64decode(item_model.archive_base64)
        recipe = json.loads(contents[item["entry"]])
        if not isinstance(recipe, dict):
            raise TypeError("集成配方必须是对象")
        if recipe.get("schema_version", 1) != 1:
            raise ValueError("集成配方 schema_version 不支持")
        kind = {"tool-apis": "tool_api", "tool_api": "tool_api", "mcp": "mcp"}.get(
            recipe.get("kind")
        )
        if kind is None:
            raise ValueError("集成配方只支持 tool_api 或 mcp")
        raw = recipe.get("config", recipe.get("manifest", {}))
        if not isinstance(raw, dict):
            raise TypeError("集成配方 config 必须是对象")
        allowed = (
            {
                "method",
                "url",
                "auth_kind",
                "auth_header_name",
                "parameters_schema",
                "query_template",
                "body_template",
            }
            if kind == "tool_api"
            else {"transport", "url", "command", "arguments", "timeout_ms"}
        )
        config = {k: v for k, v in raw.items() if k in allowed}
        reject_embedded_credentials(config)
        # Credentials/environment from a remote recipe are never installed.
        config.update({k: v for k, v in overrides.items() if k in {"command", "arguments", "url"}})
        config.update(
            name=name or recipe.get("name") or item["name"],
            description=item["description"][:500],
            tags=item["tags"][:12],
            enabled=False,
        )
        parsed = (ToolApiCreateIn if kind == "tool_api" else McpCreateIn).model_validate(config)
        return kind, parsed.name, parsed

    async def install(self, source_id, revision, key, expected_digest, names, overrides):
        async with self.lock:
            snapshot, item = self.item(source_id, revision, key)
            if expected_digest != item["digest"]:
                raise HTTPException(409, "浏览版本与导入内容不一致，请重新打开详情")
            parts = self.dependencies(snapshot, item)
            receipts = await self.receipts()
            current_ids = {r["id"] for r in await PortableCatalog(self.manager).list()}
            for receipt in receipts:
                if (
                    receipt["source_id"] == source_id
                    and receipt.get("digest") == item["digest"]
                    and receipt["resource_id"] == item["id"]
                    and all(r["integration_id"] in current_ids for r in receipt["resources"])
                ):
                    return receipt
            previous = [
                r
                for receipt in receipts
                if receipt["source_id"] == source_id
                for r in receipt["resources"]
            ]
            known = {r["key"]: r for r in previous if r["integration_id"] in current_ids}
            prepared = []
            for part in parts:
                for prior in previous:
                    if (
                        prior["resource_id"] == part["id"]
                        and prior["version"] == part["version"]
                        and prior["digest"] != part["digest"]
                    ):
                        raise HTTPException(
                            409, "同一资源版本的内容发生变化，请来源维护者提升版本号"
                        )
                old = known.get(part["key"])
                if old and old["digest"] == part["digest"]:
                    prepared.append((part, old, None))
                else:
                    prepared.append(
                        (
                            part,
                            None,
                            await self.prepare(
                                snapshot,
                                part,
                                names.get(part["key"], "").strip(),
                                overrides.get(part["key"], {}),
                            ),
                        )
                    )
            targets = await PortableCatalog(self.manager).list()
            names_seen = set()
            for part, old, config in prepared:
                if old:
                    continue
                kind, name, _ = config
                token = (KINDS[kind], name)
                if token in names_seen or any((t["kind"], t["name"]) == token for t in targets):
                    raise HTTPException(
                        409,
                        {
                            "message": "名称已存在，请在详情中修改导入名称。",
                            "resource_key": part["key"],
                        },
                    )
                names_seen.add(token)
            identity = digest([source_id, item["id"], item["digest"], time.time_ns()])
            value = {
                "id": identity,
                "source_id": source_id,
                "repository": snapshot["repository"],
                "revision": revision,
                "resource_id": item["id"],
                "digest": item["digest"],
                "created_at": utc_now().isoformat(),
                "resources": [],
            }
            placed = []
            async with self.manager.database.sessions() as session:
                session.defer_commit = True
                try:
                    for part, old, prepared_part in prepared:
                        if old:
                            value["resources"].append(old)
                            continue
                        kind, name, config = prepared_part
                        integration_id = str(uuid5(NAMESPACE_URL, identity + part["key"]))
                        if kind == "skill":
                            catalog = self.manager.skill_catalog(session)
                            result = await catalog.create(
                                MemoryUpload(config),
                                enabled=False,
                                tags=part["tags"][:12],
                                identity=integration_id,
                            )
                            placed.append(
                                catalog.storage.final_path(result.id, result.latest_version)
                            )
                        elif kind == "mcp":
                            await self.manager.mcp_catalog(session).create(
                                config, identity=integration_id
                            )
                        else:
                            await self.manager.tool_catalog(session).create(
                                config, identity=integration_id
                            )
                        value["resources"].append(
                            {
                                "key": part["key"],
                                "resource_id": part["id"],
                                "version": part["version"],
                                "digest": part["digest"],
                                "kind": KINDS[kind],
                                "name": name,
                                "integration_id": integration_id,
                                "enabled": False,
                            }
                        )
                    existing = await session.get(TransferReceipt, "resource-library:" + identity)
                    if existing:
                        return existing.result
                    session.add(
                        TransferReceipt(
                            id="resource-library:" + identity, result=value, created_at=utc_now()
                        )
                    )
                    session.defer_commit = False
                    await session.commit()
                    return value
                except BaseException:
                    await session.rollback()
                    for path in placed:
                        shutil.rmtree(path, ignore_errors=True)
                    raise


def quote_path(path):
    from urllib.parse import quote

    return quote(path, safe="/")


def reject_embedded_credentials(value):
    """Recipes describe configuration; actual credentials are supplied locally later."""
    from urllib.parse import parse_qsl, urlsplit

    sensitive = re.compile(r"secret|password|token|api[_-]?key|authorization", re.IGNORECASE)

    def inspect(current):
        if isinstance(current, dict):
            for key, child in current.items():
                if (
                    sensitive.search(key)
                    and child
                    and not (isinstance(child, str) and child.startswith(("${", "{{", "<")))
                ):
                    raise ValueError("集成配方含固定凭据，请使用占位符并在本机填写")
                inspect(child)
        elif isinstance(current, list):
            for child in current:
                inspect(child)
        elif isinstance(current, str) and (
            "-----BEGIN " in current or current.startswith("Bearer ")
        ):
            raise ValueError("集成配方含固定凭据")

    inspect(value)
    if value.get("url"):
        url = urlsplit(value["url"])
        if (
            url.username
            or url.password
            or any(sensitive.search(k) for k, _ in parse_qsl(url.query))
        ):
            raise ValueError("配方服务地址不能携带凭据")
