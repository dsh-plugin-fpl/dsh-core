"""Convert the provider's offline catalog and test history into owned Core records."""

import hashlib
import json
import re
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID, uuid5

from .catalog import Catalog
from .credentials import ProviderCredentials
from .file_library import FileLibrary
from .store import encode
from .retired_providers import RETIRED_PROVIDER_KINDS, retire_providers


def parsed(value, default=None):
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def timestamp(value):
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return 0.0


def import_catalog(
    store, access, rows, source_owner, source_id, transfer, aliases, checkpoint=lambda: None
):
    retire_providers(store)
    credentials = ProviderCredentials(store, access.cipher)
    # An offline import has no connected DSH model registrations or execution runtime.
    catalog = Catalog(
        SimpleNamespace(store=store), credentials, SimpleNamespace(model_catalog=list)
    )
    value = catalog.saved()
    revision = value.pop("revision")
    current_suppliers = {row["id"] for row in value["suppliers"]}
    current_providers = {row["id"] for row in value["providers"]}
    current_models = {
        model["id"] for provider in value["providers"] for model in provider["models"]
    }
    secrets = []
    for row in rows.get("model_suppliers", []):
        if row["owner_user_id"] != source_owner:
            continue
        original_id = row["id"]
        identity = aliases.setdefault(
            original_id,
            str(uuid5(UUID(source_id), "supplier:" + original_id))
            if original_id in current_suppliers
            else original_id,
        )
        if identity not in {r["id"] for r in value["suppliers"]}:
            value["suppliers"].append(
                {
                    "id": identity,
                    "name": row["name"],
                    "origin": "user",
                    "template_key": None,
                    "sort_order": row["sort_order"],
                }
            )
    entries = rows.get("model_entries", [])
    pending_credentials = []
    for row in rows.get("model_providers", []):
        if row["owner_user_id"] != source_owner:
            continue
        if row["kind"] in RETIRED_PROVIDER_KINDS:
            # Only keep an in-memory redaction value for imported historical logs.
            # Never persist a credential or routing record for a retired provider.
            if row.get("api_key_ciphertext"):
                secrets.append(transfer.decrypt(row["api_key_ciphertext"].encode()).decode())
            continue
        original_id = row["id"]
        identity = aliases.setdefault(
            original_id,
            str(uuid5(UUID(source_id), "provider:" + original_id))
            if original_id in current_providers
            else original_id,
        )
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", identity):
            identity = "legacy_" + identity
            aliases[original_id] = identity
        models = []
        for item in entries:
            if item["provider_id"] != row["id"]:
                continue
            original_model = item["id"]
            model_id = aliases.setdefault(
                original_model,
                str(uuid5(UUID(source_id), "model:" + original_model))
                if identity != row["id"] or original_model in current_models
                else original_model,
            )
            model = catalog.model(
                identity,
                {
                    **item,
                    "capabilities": parsed(item["capabilities"], []),
                    "categories": parsed(item["categories"], []),
                },
                model_id,
            )
            models.append(model)
        provider = {
            "id": identity,
            "name": row["name"] + ("（旧 FPL）" if identity != row["id"] else ""),
            "kind": row["kind"],
            "base_url": row["base_url"],
            "enabled": bool(row["enabled"]),
            "supplier_id": aliases.get(row["supplier_id"], row["supplier_id"]),
            "origin": "user",
            "template_key": None,
            "managed_fields": [],
            "models": models,
        }
        if identity not in {p["id"] for p in value["providers"]}:
            value["providers"].append(provider)
        if row["api_key_ciphertext"]:
            plain = transfer.decrypt(row["api_key_ciphertext"].encode()).decode()
            secrets.append(plain)
            pending_credentials.append((identity, plain))
    for row in rows.get("model_account_defaults", []):
        if row["owner_user_id"] == source_owner:
            for capability, identity in parsed(row["model_ids"], {}).items():
                value["default_model_ids"].setdefault(capability, aliases.get(identity, identity))
    # Old insecure public HTTP endpoints remain in imported history, never active routing.
    from .extracted.runtime.http.outbound_urls import (
        normalize_provider_base_url,
    )

    for provider in value["providers"]:
        if provider["kind"].startswith("local_"):
            continue
        try:
            normalize_provider_base_url(provider["base_url"])
        except ValueError:
            provider["legacy_base_url"] = provider["base_url"]
            provider["base_url"] = "https://migration-disabled.invalid"
            provider["enabled"] = False
            provider["name"] = provider["name"][:95] + "（需重新配置地址）"
    # Keep unsupported old defaults in imported history without selecting a disabled model.
    enabled = {
        m["id"]: m for p in value["providers"] if p["enabled"] for m in p["models"] if m["enabled"]
    }
    value["default_model_ids"] = {
        capability: identity
        for capability, identity in value["default_model_ids"].items()
        if identity in enabled and capability in enabled[identity]["capabilities"]
    }
    checkpoint()
    catalog.save(value, revision)
    for identity, plain in pending_credentials:
        key = "credential:PROVIDER_" + identity.replace("-", "_")
        saved = store.config(key)
        store.save_config(key, credentials.encrypted(plain), saved["revision"])
    return catalog, secrets


def redact(value, secrets):
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if key.casefold()
                in {"api_key", "authorization", "password", "secret", "access_token"}
                else redact(item, secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value


def original(bundle, component, manifest, storage_key, owner=None, *, missing_ok=False):
    from pathlib import Path

    if not storage_key:
        return None
    candidates = [
        relative
        for relative in manifest["files"]
        if relative == storage_key or Path(relative).name == storage_key
    ]
    if owner:
        owned = [p for p in candidates if owner in Path(p).parts]
        if owned:
            candidates = owned
    if not candidates and missing_ok:
        return None
    if len(candidates) != 1:
        raise ValueError("历史原件未找到或归属不唯一")
    relative = candidates[0]
    return Path(bundle) / component / "files" / relative, manifest["files"][relative]


def import_conversations(
    store,
    resources,
    bundle,
    manifest,
    rows,
    source_owner,
    source_id,
    aliases,
    checkpoint=lambda: None,
):
    result = 0
    attachments = rows.get("model_test_attachments", [])
    for row in rows.get("model_test_conversations", []):
        if row["owner_user_id"] != source_owner:
            continue
        identity = aliases.get(row["id"], row["id"])
        current = store.config("conversation:" + identity)
        if row["id"] not in aliases:
            aliases[row["id"]] = (
                str(uuid5(UUID(source_id), "conversation:" + row["id"]))
                if current["revision"]
                else row["id"]
            )
        identity = aliases[row["id"]]
        checkpoint()
        messages = []
        draft = []
        converted = {}
        for asset in attachments:
            if asset["conversation_id"] != row["id"]:
                continue
            path, info = original(bundle, "models", manifest, asset["storage_key"], source_owner)
            value = FileLibrary(resources).import_original(
                {
                    "id": asset["id"],
                    "display_name": asset["original_filename"],
                    "original_filename": asset["original_filename"],
                    "mime_type": asset["mime_type"],
                    "byte_size": asset["byte_size"],
                },
                path,
                info["sha256"],
                source_id,
            )
            converted[asset["id"]] = {
                "id": value["id"],
                "filename": asset["original_filename"],
                "mime_type": asset["mime_type"],
                "byte_size": asset["byte_size"],
                "width": asset["width"],
                "height": asset["height"],
                "url": "/v1/resources/" + value["id"] + "/content",
            }
            if asset["message_id"] is None:
                draft.append(converted[asset["id"]])
        for message in sorted(
            (m for m in rows.get("model_test_messages", []) if m["conversation_id"] == row["id"]),
            key=lambda m: m["position"],
        ):
            messages.append(
                {
                    **message,
                    "attachments": [
                        converted[a["id"]] for a in attachments if a["message_id"] == message["id"]
                    ],
                    "status": message["status"]
                    if message["status"] in {"completed", "error", "cancelled"}
                    else "error",
                }
            )
        value = {
            **row,
            "id": identity,
            "model_id": aliases.get(row["model_id"], row["model_id"]),
            "model_slug": "",
            "messages": messages,
            "draft_attachments": draft,
        }
        saved = store.config("conversation:" + identity)
        if not saved["revision"]:
            store.save_config("conversation:" + identity, value, 0)
        result += 1
    return result


def import_tasks(
    store,
    resources,
    bundle,
    manifest,
    rows,
    source_owner,
    source_id,
    aliases,
    checkpoint=lambda: None,
):
    count = 0
    connections = {row["id"]: row for row in store.config("connections", [])["value"]}
    for table in [
        "model_inference_tasks",
        "model_test_generation_tasks",
        "model_test_transcription_tasks",
        "video_enhancement_tasks",
    ]:
        for row in rows.get(table, []):
            if row.get("owner_user_id") != source_owner:
                continue
            count += 1
            identity = aliases.get(row["id"], row["id"])
            existing = store.db.execute(
                "SELECT value FROM tasks WHERE id=?", (identity,)
            ).fetchone()
            if existing:
                prior = json.loads(existing[0])
                if prior.get("imported_source") == source_id:
                    continue
                identity = aliases.setdefault(
                    row["id"], str(uuid5(UUID(source_id), "task:" + identity))
                )
            checkpoint()
            provider = aliases.get(row.get("provider_id"), row.get("provider_id"))
            connection = connections.get(
                provider,
                {
                    "id": provider or "legacy",
                    "kind": row.get("provider_kind", "openai"),
                    "name": row.get("provider_name", "旧 FPL"),
                    "base_url": "",
                    "enabled": False,
                    "credential_ref": "",
                    "models": [],
                },
            )
            payload = parsed(row.get("request_payload"), {})
            kind = row.get("kind", row.get("generation_kind")) or row.get(
                "kind",
                "audio"
                if table.endswith("transcription_tasks")
                else "video"
                if table == "video_enhancement_tasks"
                else "image",
            )
            capability = {
                "image": "image_generation",
                "video": "video_generation",
                "audio": "speech_recognition",
                "transcription": "speech_recognition",
            }.get(kind, kind)
            if table == "video_enhancement_tasks":
                capability = "video_enhancement"
            if capability not in {
                "chat",
                "vision",
                "tools",
                "image_generation",
                "video_generation",
                "speech_synthesis",
                "speech_recognition",
                "video_enhancement",
                "music_generation",
                "end_to_end_speech",
            }:
                capability = "image_generation"
            request = {
                "request_id": "legacy-" + row["id"],
                "application_id": "core",
                "session_id": "legacy-" + row["id"],
                "kind": "local" if str(connection["kind"]).startswith("local_") else "model",
                "capability": capability,
                "connection_id": provider or "legacy",
                "model": row.get("model_slug", row.get("model", "")),
                "prompt": payload.get("prompt", payload.get("text", ""))
                if isinstance(payload, dict)
                else "",
                "parameters": payload if isinstance(payload, dict) else {},
                "resource_ids": [],
                "tools": [],
                "messages": [],
            }
            # Owned reference inputs are independent resources, separate from task outputs.
            input_resources = {}
            for asset in rows.get("model_inference_assets", []):
                if asset["task_id"] != row["id"]:
                    continue
                incoming = original(
                    bundle, "models", manifest, asset["storage_key"], source_owner, missing_ok=True
                )
                if incoming is None:
                    continue
                asset_path, asset_info = incoming
                resource = FileLibrary(resources).import_original(
                    {
                        "id": asset["id"],
                        "original_filename": asset["filename"],
                        "mime_type": asset["mime_type"],
                        "byte_size": asset["byte_size"],
                    },
                    asset_path,
                    asset_info["sha256"],
                    source_id,
                )
                input_resources[asset["id"]] = resource["id"]
            ordered = (
                list(
                    dict.fromkeys(
                        [
                            payload.get("reference_asset_id"),
                            *payload.get("asset_ids", []),
                            *input_resources,
                        ]
                    )
                )
                if isinstance(payload, dict)
                else list(input_resources)
            )
            request["resource_ids"] = [
                input_resources[asset] for asset in ordered if asset in input_resources
            ]
            status = row.get("status")
            status = {"success": "completed", "error": "failed"}.get(status, status)
            if status not in {"completed", "failed", "cancelled"}:
                status = "interrupted"
            result = parsed(row.get("result"), {}) or {}
            result = result if isinstance(result, dict) else {"legacy_result": result}
            output_key = row.get("result_storage_key") or row.get("storage_key")
            if output_key:
                path, info = original(bundle, "models", manifest, output_key, source_owner)
                resource = FileLibrary(resources).import_original(
                    {
                        "id": str(uuid5(UUID(source_id), "task-output:" + row["id"])),
                        "original_filename": row.get("result_filename")
                        or row.get("filename")
                        or path.name,
                        "mime_type": row.get("result_mime_type")
                        or row.get("mime_type")
                        or "application/octet-stream",
                        "byte_size": info["bytes"],
                    },
                    path,
                    info["sha256"],
                    source_id,
                )
                result["resources"] = [resource]
            if table == "model_test_transcription_tasks":
                result["segments"] = parsed(row.get("result_segments"), [])
                result["transcription"] = parsed(row.get("result_detail"), {})
                if row.get("input_storage_key"):
                    source_asset = row.get("source_asset_id")
                    existing_asset = (
                        store.db.execute(
                            "SELECT id FROM resources WHERE id=?", (source_asset,)
                        ).fetchone()
                        if source_asset
                        else None
                    )
                    incoming = original(
                        bundle,
                        "models",
                        manifest,
                        row["input_storage_key"],
                        source_owner,
                        missing_ok=True,
                    )
                    if existing_asset:
                        request["resource_ids"] = [existing_asset[0]]
                    elif incoming:
                        input_path, input_info = incoming
                        input_resource = FileLibrary(resources).import_original(
                            {
                                "id": str(uuid5(UUID(source_id), "task-input:" + row["id"])),
                                "original_filename": row.get("input_filename") or input_path.name,
                                "mime_type": row.get("input_mime_type")
                                or "application/octet-stream",
                                "byte_size": input_info["bytes"],
                            },
                            input_path,
                            input_info["sha256"],
                            source_id,
                        )
                        request["resource_ids"] = [input_resource["id"]]
                    else:
                        result["input_unavailable"] = True
                        result["input_unavailable_reason"] = (
                            "原项目已清理此任务的临时上传文件；转写结果与历史元数据已保留"
                        )
            task = {
                "id": identity,
                "request": request,
                "connection": connection,
                "status": status,
                "progress": row.get("progress", 100 if status == "completed" else 0),
                "text": row.get("result_text")
                or row.get("text", row.get("transcript", ""))
                or result.get("text", ""),
                "result": result,
                "error": {
                    "code": "legacy_interrupted" if status == "interrupted" else "legacy_error",
                    "message": row.get("error_message") or "旧记录未完成，不会自动重新执行",
                }
                if status in {"failed", "interrupted"}
                else None,
                "created_at": timestamp(row.get("created_at")),
                "updated_at": timestamp(row.get("updated_at")),
                "provider_task_id": row.get("provider_task_id", "") or "",
                "result_url": row.get("result_url", "") or "",
                "imported_source": source_id,
            }
            fingerprint = hashlib.sha256(encode(request).encode()).hexdigest()
            store.db.execute(
                "INSERT INTO tasks VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                (
                    identity,
                    encode(["core", request["session_id"]]),
                    request["request_id"],
                    fingerprint,
                    status,
                    encode(task),
                ),
            )
    return count
