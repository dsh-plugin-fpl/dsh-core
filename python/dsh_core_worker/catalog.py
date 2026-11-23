"""FPL catalog view, backed by Core's own atomic configuration records."""

from copy import deepcopy
from uuid import uuid4

from .contracts import PROVIDERS, Connection, CoreError
from .models import PROVIDER_CAPABILITIES

CATEGORIES = [
    ("text_generation", "文本生成", "text"),
    ("other_general", "通用能力", "text"),
    ("image_generation", "图像生成", "image"),
    ("video_generation", "视频生成", "video"),
    ("video_enhancement", "视频增强", "video"),
    ("speech_synthesis", "语音合成", "audio"),
    ("speech_recognition", "语音识别", "audio"),
    ("music_generation", "音乐生成", "audio"),
    ("end_to_end_speech", "实时语音", "audio"),
]


class Catalog:
    def __init__(self, runtime, credentials, bridges):
        self.runtime, self.credentials, self.bridges = runtime, credentials, bridges

    def saved(self):
        state = self.runtime.store.config(
            "catalog", {"providers": [], "suppliers": [], "default_model_ids": {}}
        )
        return {"revision": state["revision"], **deepcopy(state["value"])}

    def view(self):
        value = self.saved()
        for provider in value["providers"]:
            provider["has_api_key"] = bool(
                self.credentials.secret("PROVIDER_" + provider["id"].replace("-", "_"))
            )
            provider["api_key_hint"] = "已配置" if provider["has_api_key"] else ""
        for native in self.bridges.model_catalog():
            identity = native["id"]
            value["providers"].append(
                {
                    "id": identity,
                    "name": "DSH · " + native["name"],
                    "kind": "deepseek",
                    "origin": "role_template",
                    "managed_fields": ["name", "kind", "base_url", "api_key", "models"],
                    "enabled": True,
                    "base_url": "",
                    "has_api_key": False,
                    "api_key_hint": "",
                    "supplier_id": None,
                    "models": [
                        self.model(identity, {**m, "slug": m["id"]}, f"{identity}/{m['id']}")
                        for m in native["models"]
                    ],
                }
            )
        return value

    @staticmethod
    def model(provider, body, identity=None):
        return {
            "id": identity or str(uuid4()),
            "provider_id": provider,
            "slug": str(body.get("slug", "")).strip(),
            "label": str(body.get("label", ""))[:160],
            "description": str(body.get("description", ""))[:255],
            "capabilities": body.get("capabilities", ["chat"]),
            "categories": body.get("categories", []),
            "enabled": bool(body.get("enabled", True)),
            "origin": "user",
            "availability": "ready",
            "managed_fields": [],
            "template_key": None,
        }

    def find(self, identity):
        for provider in self.view()["providers"]:
            for model in provider["models"]:
                if model["id"] == identity:
                    return provider, model
        raise CoreError("模型不存在，请重新选择", "not_found")

    def execution(self, identity, **values):
        provider, model = self.find(identity)
        if not provider["enabled"] or not model["enabled"]:
            raise CoreError("模型连接已停用", "connection_disabled")
        local = provider["kind"].startswith("local_")
        return {
            "connection_id": provider["id"],
            "model": model["slug"],
            "kind": "local" if local else "model",
            **values,
        }

    def save(self, value, revision, secret_update=None):
        connections = []
        for provider in value["providers"]:
            if (
                not provider["name"].strip()
                or len(provider["name"]) > 120
                or provider["kind"] not in PROVIDERS
            ):
                raise CoreError("连接名称或协议类型无效")
            models = provider["models"]
            if len({m["id"] for m in models}) != len(models) or len(
                {m["slug"] for m in models}
            ) != len(models):
                raise CoreError("同一连接中模型标识不能重复")
            for model in models:
                if not model["slug"] or len(model["slug"]) > 160:
                    raise CoreError("模型标识无效")
                if any(
                    c not in PROVIDER_CAPABILITIES[provider["kind"]] for c in model["capabilities"]
                ):
                    raise CoreError("模型能力与接入协议不匹配")
                if any(c not in {x[0] for x in CATEGORIES} for c in model["categories"]):
                    raise CoreError("模型分类无效")
            connection = Connection.model_validate(
                {
                    "id": provider["id"],
                    "name": provider["name"],
                    "kind": provider["kind"],
                    "base_url": provider["base_url"],
                    "enabled": provider["enabled"],
                    "credential_ref": "PROVIDER_" + provider["id"].replace("-", "_"),
                    "models": [
                        {
                            "id": m["slug"],
                            "label": m["label"],
                            "capabilities": m["capabilities"],
                            "enabled": m["enabled"],
                        }
                        for m in models
                    ],
                }
            ).model_dump()
            provider["base_url"] = connection["base_url"]
            connections.append(connection)
        if any(
            p.get("supplier_id") and p["supplier_id"] not in {s["id"] for s in value["suppliers"]}
            for p in value["providers"]
        ):
            raise CoreError("供应商分组不存在")
        view = {
            "providers": value["providers"]
            + self.view()["providers"][len(self.saved()["providers"]) :]
        }
        defaults = {}
        valid_defaults = {}
        for capability, model_id in value["default_model_ids"].items():
            found = next(
                ((p, m) for p in view["providers"] for m in p["models"] if m["id"] == model_id),
                None,
            )
            if found is None:
                previous = self.runtime.store.config("defaults", {})["value"].get(capability)
                if (
                    previous
                    and str(previous.get("connection_id", "")).startswith("harness:")
                    and model_id == previous["connection_id"] + "/" + previous["model"]
                ):
                    defaults[capability] = previous
                    valid_defaults[capability] = model_id
                continue
            provider, model = found
            if (
                capability not in model["capabilities"]
                or not provider["enabled"]
                or not model["enabled"]
            ):
                raise CoreError("默认模型不支持所选用途或已停用")
            valid_defaults[capability] = model_id
            defaults[capability] = {
                "kind": "local" if provider["kind"].startswith("local_") else "model",
                "connection_id": provider["id"],
                "model": model["slug"],
            }
        value["default_model_ids"] = valid_defaults
        store = self.runtime.store
        store.db.execute("BEGIN IMMEDIATE")
        try:
            current = store.config("catalog", {})
            if current["revision"] != revision:
                raise CoreError(
                    "目录已更新，输入已保留，请核对后重试", "revision_conflict", current=self.view()
                )
            from .store import encode

            updates = [("catalog", value), ("connections", connections), ("defaults", defaults)]
            if secret_update is not None:
                reference, secret = secret_update
                updates.append(("credential:" + reference, self.credentials.encrypted(secret)))
            for key, data in updates:
                existing = store.config(key)
                store.db.execute(
                    "INSERT INTO configs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,value=excluded.value",
                    (key, existing["revision"] + 1, encode(data)),
                )
            store.db.execute("COMMIT")
        except BaseException:
            store.db.execute("ROLLBACK")
            raise
        return self.view()

    def mutate(self, entity, action, body, identity=None, parent=None, revision=None):
        value = self.saved()
        expected = body.get("expected_catalog_revision", revision)
        if expected != value["revision"]:
            raise CoreError(
                "目录已更新，请核对后重新保存", "revision_conflict", current=self.view()
            )
        items = value["suppliers"] if entity == "supplier" else value["providers"]
        provider = None
        if entity == "model":
            provider = next(
                (
                    p
                    for p in value["providers"]
                    if p["id"] == parent or any(m["id"] == identity for m in p["models"])
                ),
                None,
            )
            if not provider:
                raise CoreError("连接不存在", "not_found")
            items = provider["models"]
        item = next((i for i in items if i["id"] == identity), None)
        if action != "create" and not item:
            raise CoreError("配置项不存在", "not_found")
        result_id = identity or ("p-" if entity == "provider" else "") + str(uuid4())
        secret = None
        if action == "delete":
            items.remove(item)
            if entity == "supplier":
                for p in value["providers"]:
                    if p.get("supplier_id") == identity:
                        p["supplier_id"] = None
        elif entity == "supplier":
            name = str(body.get("name", "")).strip()
            if not name or len(name) > 120:
                raise CoreError("供应商名称无效")
            if item:
                item["name"] = name
            else:
                items.append({"id": result_id, "name": name, "origin": "user"})
        elif entity == "model":
            updated = self.model(provider["id"], body, result_id)
            if item:
                item.update(updated)
            else:
                items.append(updated)
        else:
            updated = {
                **(item or {"id": result_id, "models": [], "origin": "user", "managed_fields": []}),
                **{
                    k: body[k]
                    for k in ("name", "kind", "base_url", "enabled", "supplier_id")
                    if k in body
                },
            }
            updated.setdefault("base_url", "")
            updated.setdefault("enabled", True)
            updated.setdefault("supplier_id", None)
            if item:
                item.update(updated)
            else:
                items.append(updated)
            if body.get("api_key"):
                secret = body["api_key"]
        result = self.save(
            {k: v for k, v in value.items() if k != "revision"},
            expected,
            ("PROVIDER_" + result_id.replace("-", "_"), secret) if secret is not None else None,
        )
        if action == "delete":
            return {"deleted": True, "revision": result["revision"]}
        return {
            "revision": result["revision"],
            entity: next(
                i for p in result["providers"] for i in p["models"] if i["id"] == result_id
            )
            if entity == "model"
            else next(
                i
                for i in result["suppliers" if entity == "supplier" else "providers"]
                if i["id"] == result_id
            ),
        }
