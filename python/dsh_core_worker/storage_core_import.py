"""Core owns conversion of legacy accounts, catalog, history, library and integrations."""

import asyncio
import hashlib
import json
from pathlib import Path

from cryptography.fernet import Fernet

from .access import Access
from .accounts import Accounts
from .file_library import FileLibrary
from .private_files import atomic_private
from .resources import Resources
from .storage_accounts import import_accounts
from .storage_history import StorageHistory
from .storage_model_import import (
    import_catalog,
    import_conversations,
    import_tasks,
    original,
    parsed,
    redact,
)
from .storage_records import (
    copy_component_files,
    merge_owned,
    plan_identifiers,
    read_component,
    translated,
)
from .store import Store, encode
from .retired_providers import RETIRED_PROVIDER_KINDS


def owned(rows, owner, primary=False):
    """Use explicit owner columns and parent IDs, never another account's default."""
    result = {name: [] for name in rows}
    identities = {owner}
    pending = []
    for name, records in rows.items():
        for row in records:
            claimed = row.get("owner_user_id", row.get("user_id"))
            if claimed is not None:
                if claimed == owner:
                    result[name].append(row)
                    if row.get("id"):
                        identities.add(row["id"])
            elif row.get("id") == owner:
                result[name].append(row)
            else:
                pending.append((name, row))
    while pending:
        rest = []
        for name, row in pending:
            references = {
                value
                for key, value in row.items()
                if key != "id" and key.endswith("_id") and isinstance(value, str)
            }
            if references & identities:
                result[name].append(row)
                if row.get("id"):
                    identities.add(row["id"])
            else:
                rest.append((name, row))
        if len(rest) == len(pending):
            break
        pending = rest
    if primary:
        # Accountless machine installation/definition datasets remain machine-owned.
        machine = {
            "local_model_installations",
            "local_model_operations",
            "model_quota_policy",
            "model_catalog_provisioning",
            "model_role_templates",
            "runtime_admissions",
        }
        for name, row in pending:
            if name in machine:
                result[name].append(row)
    return result


def asset_original(bundle, manifest, row):
    published = manifest.get("originals_by_id", {}).get(row["id"])
    if published:
        if published not in manifest["files"]:
            raise ValueError("素材原件声明不完整")
        return Path(bundle) / "assets/files" / published, manifest["files"][published]
    return original(bundle, "assets", manifest, row["storage_key"], row["owner_user_id"])


def archive(history, dataset, records, source_id, aliases, cipher, transfer, secrets):
    for ordinal, row in enumerate(records):
        value = translated(row, aliases)
        identity = str(row.get("id") or hashlib.sha256(encode(row).encode()).hexdigest())
        previous = history.get(dataset, identity)
        for field, item in list(value.items()):
            if (
                dataset == "model_providers"
                and row.get("kind") in RETIRED_PROVIDER_KINDS
                and field == "api_key_ciphertext"
            ):
                value[field] = None
                continue
            if field.endswith("ciphertext") and item:
                plain = transfer.decrypt(item.encode())
                prior = previous.get(field) if previous else None
                value[field] = (
                    prior
                    if prior and cipher.decrypt(prior.encode()) == plain
                    else cipher.encrypt(plain).decode()
                )
            elif field in {
                "request_payload",
                "response_payload",
                "request_options",
                "result",
                "result_detail",
                "result_segments",
                "model_ids",
                "capabilities",
                "categories",
                "parameters_schema",
                "query_template",
                "body_template",
                "tags",
                "arguments",
                "header_keys",
                "environment_keys",
                "manifest",
                "payload",
                "checkpoint",
            } and isinstance(item, str):
                value[field] = parsed(item, item)
        if dataset == "llm_call_logs":
            value = redact(value, secrets)
            value.update(
                reproduction_supported=False,
                reproduction_reason="旧 FPL 的历史快照，不会自动重新执行",
            )
        history.add(dataset, identity, source_id, value)


async def import_integrations(
    root,
    rows,
    source_owner,
    source_id,
    aliases,
    transfer,
    access,
    manifest,
    bundle,
    receipt,
    checkpoint,
):
    from .integrations.database import Database
    from .integrations.external_schema import migrate as migrate_external
    from .integrations.storage.external_models import ExternalConnection
    from .integrations.storage.models import (
        IntegrationCheck,
        McpDiscoveredTool,
        McpIntegration,
        SkillIntegration,
        SkillVersion,
        ToolApiIntegration,
    )
    from .integrations.storage.transfer_receipts import TransferReceipt

    models = (
        ToolApiIntegration,
        McpIntegration,
        McpDiscoveredTool,
        SkillIntegration,
        SkillVersion,
        IntegrationCheck,
        ExternalConnection,
        TransferReceipt,
    )
    database = Database(root / "integrations")
    try:
        await database.migrate()
        await migrate_external(database)
    finally:
        await database.close()
    names = {model.__tablename__ for model in models}
    selected = {name: values for name, values in rows.items() if name in names}
    mapping = {**aliases, source_owner: "local-owner"}
    target = root / "integrations"
    prefixes = [(manifest["source_root"], str(target))]

    def rekey(row):
        for key, value in list(row.items()):
            if key.endswith("ciphertext") and value:
                cache = receipt.setdefault("integration_ciphers", {})
                if value not in cache:
                    cache[value] = access.cipher.encrypt(transfer.decrypt(value.encode())).decode()
                    checkpoint()
                row[key] = cache[value]
        return row

    # Explicit projections retain extra legacy attributes in imported history.
    allowed = {
        model.__tablename__: {column.name for column in model.__table__.columns} for model in models
    }
    projected = {
        name: [{key: value for key, value in row.items() if key in allowed[name]} for row in values]
        for name, values in selected.items()
    }
    planned = receipt.setdefault("integration_planned", [])
    if source_owner not in planned:
        plan_identifiers(database.path, models, projected, mapping, source_id)
        aliases.update({key: value for key, value in mapping.items() if key != source_owner})
        planned.append(source_owner)
        checkpoint()
    result = merge_owned(database.path, models, projected, mapping, prefixes, rekey)
    files = {
        path: record
        for path, record in manifest["files"].items()
        if source_owner in Path(path).parts
    }
    copy_component_files(bundle, "integrations", target, {**manifest, "files": files}, mapping)
    return result


def import_bundle(root, bundle, primary_source, key_file, receipt_file):
    root, bundle = Path(root), Path(bundle)
    key_file = Path(key_file)
    if key_file.is_symlink() or key_file.stat().st_mode & 0o077:
        raise ValueError("迁移密钥必须仅当前用户可读")
    header = json.loads((bundle / "manifest.json").read_text())
    if header.get("version") != 1 or header.get("service") != "core":
        raise ValueError("Core 迁移包格式无效")
    source_id = header["source_id"]
    parts = {
        name: read_component(bundle, name)
        for name in ["identity", "assets", "models", "integrations"]
    }
    accounts = Accounts(root / "identity")
    users = parts["identity"][1]["users"]
    if primary_source not in {row["id"] for row in users}:
        raise ValueError("主要来源账号未通过原身份库核对")
    mapping = import_accounts(accounts, users, primary_source, source_id)
    transfer = Fernet(Path(key_file).read_bytes())
    receipt_file = Path(receipt_file)
    receipt = (
        json.loads(receipt_file.read_text())
        if receipt_file.exists()
        else {
            "version": 1,
            "source": source_id,
            "primary_source": primary_source,
            "accounts": mapping,
            "aliases": {},
            "completed_accounts": [],
        }
    )
    if receipt["accounts"] != mapping or receipt["source"] != source_id:
        raise ValueError("账号迁移回执不匹配")
    atomic_private(receipt_file, encode(receipt).encode())

    def checkpoint():
        atomic_private(receipt_file, encode(receipt).encode())

    results = {}
    for profile in users:
        source_owner = profile["id"]
        target_owner = mapping[source_owner]
        if source_owner in receipt["completed_accounts"]:
            results[target_owner] = receipt.get("results", {}).get(target_owner, {})
            continue
        tenant = root if source_owner == primary_source else root / "accounts" / target_owner
        access, store = Access(tenant / "access"), Store(tenant / "state")
        resources = Resources(store)
        library, history = (
            FileLibrary(resources, initialize=True),
            StorageHistory(store.root, initialize=True),
        )
        aliases = receipt["aliases"].setdefault(source_owner, {source_owner: target_owner})
        try:
            models = owned(parts["models"][1], source_owner, source_owner == primary_source)
            _catalog, secrets = import_catalog(
                store, access, models, source_owner, source_id, transfer, aliases, checkpoint
            )
            receipt.setdefault("skipped_retired_providers", {})[target_owner] = sum(
                row.get("kind") in RETIRED_PROVIDER_KINDS
                for row in models.get("model_providers", [])
            )
            receipt.setdefault("model_aliases", {})[source_owner] = {
                key: value for key, value in aliases.items() if key != source_owner
            }
            atomic_private(receipt_file, encode(receipt).encode())
            assets = owned(parts["assets"][1], source_owner)
            library.import_folders(
                translated(assets.get("visual_asset_folders", []), aliases), source_id
            )
            for row in assets.get("visual_assets", []):
                path, info = asset_original(bundle, parts["assets"][0], row)
                library.import_original(translated(row, aliases), path, info["sha256"], source_id)
            conversations = import_conversations(
                store,
                resources,
                bundle,
                parts["models"][0],
                models,
                source_owner,
                source_id,
                aliases,
                checkpoint,
            )
            tasks = import_tasks(
                store,
                resources,
                bundle,
                parts["models"][0],
                models,
                source_owner,
                source_id,
                aliases,
                checkpoint,
            )
            integrations = owned(parts["integrations"][1], source_owner)
            imported_integrations = asyncio.run(
                import_integrations(
                    tenant,
                    integrations,
                    source_owner,
                    source_id,
                    aliases,
                    transfer,
                    access,
                    parts["integrations"][0],
                    bundle,
                    receipt,
                    checkpoint,
                )
            )
            for component, rows in [
                ("identity", {"users": [profile]}),
                ("models", models),
                ("assets", assets),
                ("integrations", integrations),
            ]:
                for dataset, records in rows.items():
                    archive(
                        history,
                        dataset,
                        records,
                        source_id,
                        aliases,
                        access.cipher,
                        transfer,
                        secrets,
                    )
                # Preserve upgrade/deletion bytes as owned account data as well.
                manifest = parts[component][0]
                files = {
                    path: record
                    for path, record in manifest["files"].items()
                    if source_owner in Path(path).parts
                }
                copy_component_files(
                    bundle,
                    component,
                    store.root / "imported-files" / component,
                    {**manifest, "files": files},
                    aliases,
                )
            if source_owner == primary_source:
                for component in ["assets", "models", "integrations", "identity"]:
                    manifest = parts[component][0]
                    unknown = {
                        path: info
                        for path, info in manifest["files"].items()
                        if not any(owner in Path(path).parts for owner in mapping)
                    }
                    copy_component_files(
                        bundle,
                        component,
                        root / "private-recovery" / component,
                        {**manifest, "files": unknown},
                        {},
                    )
                for directory in ["models", "downloads"]:
                    component = "local-" + directory
                    if component not in header["components"]:
                        continue
                    manifest, _ = read_component(bundle, component)
                    copy_component_files(
                        bundle, component, store.root / "local-models" / directory, manifest, {}
                    )
                installed = [
                    row["id"]
                    for row in models.get("local_model_installations", [])
                    if row["enabled"] and row["status"] in {"installed", "ready"}
                ]
                saved = store.config("local", {"enabled": []})
                store.save_config(
                    "local",
                    {
                        "enabled": list(
                            dict.fromkeys([*saved["value"].get("enabled", []), *installed])
                        )
                    },
                    saved["revision"],
                )
            results[target_owner] = {
                "library_files": len(assets.get("visual_assets", [])),
                "conversations": conversations,
                "tasks": tasks,
                "integrations": imported_integrations,
                "datasets": history.inventory()["items"],
            }
            receipt.setdefault("results", {})[target_owner] = results[target_owner]
            receipt["completed_accounts"] = list({*receipt["completed_accounts"], source_owner})
            atomic_private(receipt_file, encode(receipt).encode())
        finally:
            store.db.close()
    receipt["results"] = results
    atomic_private(receipt_file, encode(receipt).encode())
    return {
        "imported": True,
        "accounts": mapping,
        "primary_source": primary_source,
        "results": results,
        "model_aliases": receipt.get("model_aliases", {}).get(primary_source, {}),
    }
