import json
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from cryptography.fernet import Fernet

from dsh_core_worker.accounts import Accounts, verify_password
from dsh_core_worker.file_library import FileLibrary
from dsh_core_worker.private_files import atomic_private
from dsh_core_worker.resources import Resources
from dsh_core_worker.storage_core_import import import_bundle
from dsh_core_worker.storage_io import digest
from dsh_core_worker.storage_records import read_component, translated
from dsh_core_worker.store import Store


def component(bundle, name, records, files=None):
    directory = bundle / name
    directory.mkdir(parents=True, exist_ok=True)
    datasets = {}
    for table, rows in records.items():
        path = directory / (table + ".jsonl")
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        datasets[table] = {"count": len(rows), "sha256": digest(path)}
    manifest = {"source_root": "/old/" + name, "datasets": datasets, "files": files or {}}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_core_full_import_retains_large_files_isolates_accounts_and_repeats(tmp_path):
    root, bundle = tmp_path / "core", tmp_path / "bundle"
    accounts = Accounts(root / "identity")
    primary, other, source_id, asset_id, folder_id = [str(uuid4()) for _ in range(5)]
    password = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash("old-password")
    users = [
        {
            "id": identity,
            "username": name,
            "display_name": name,
            "password_hash": password,
            "platform_role": "admin",
            "status": "active",
            "revision": 1,
        }
        for identity, name in [(primary, "old-owner"), (other, "other-owner")]
    ]
    parts = {"identity": component(bundle, "identity", {"users": users})}
    filename = f"accounts/{primary}/large.bin"
    path = bundle / "assets/files" / filename
    path.parent.mkdir(parents=True)
    with path.open("wb") as stream:
        stream.truncate(257 * 1024**2)
    info = {"bytes": path.stat().st_size, "sha256": digest(path)}
    parts["assets"] = component(
        bundle,
        "assets",
        {
            "visual_assets": [
                {
                    "id": asset_id,
                    "owner_user_id": primary,
                    "storage_key": "large.bin",
                    "original_filename": "超过上传限额的原件.bin",
                    "mime_type": "application/octet-stream",
                    "byte_size": info["bytes"],
                    "folder_id": folder_id,
                }
            ],
            "visual_asset_folders": [
                {"id": folder_id, "owner_user_id": primary, "parent_id": None, "name": "历史文件"}
            ],
        },
        {filename: info},
    )
    parts["models"] = component(bundle, "models", {})
    parts["integrations"] = component(bundle, "integrations", {})
    (bundle / "manifest.json").write_text(
        json.dumps({"version": 1, "service": "core", "source_id": source_id, "components": parts})
    )
    key, receipt = tmp_path / "key", tmp_path / "receipt.json"
    atomic_private(key, Fernet.generate_key())
    result = import_bundle(root, bundle, primary, key, receipt)
    assert result["accounts"][primary] == accounts.initial["id"]
    assert result["accounts"][other] != accounts.initial["id"]
    assert verify_password("old-password", password)
    store = Store(root / "state")
    library = FileLibrary(Resources(store))
    assert library.page()["folders"][0]["name"] == "历史文件"
    assert library.page(folder_id)["items"][0]["byte_size"] == 257 * 1024**2
    other_store = Store(root / "accounts" / other / "state")
    assert not FileLibrary(Resources(other_store)).page()["items"]
    assert not FileLibrary(Resources(other_store)).page()["folders"]
    second = import_bundle(root, bundle, primary, key, receipt)
    assert result == second
    assert len(library.page(folder_id)["items"]) == 1
    store.close()
    other_store.close()


def test_bundle_hash_and_structural_mapping_preserve_authored_text(tmp_path):
    old, new = str(uuid4()), str(uuid4())
    value = {
        "owner_user_id": old,
        "content": old,
        "data": json.dumps({"asset_id": old}),
        "map": {old: {"id": old}},
    }
    result = translated(value, {old: new})
    assert result["owner_user_id"] == new and result["content"] == old
    assert json.loads(result["data"])["asset_id"] == new
    assert result["map"][new]["id"] == new
    component(tmp_path, "test", {"example": [{"id": old}]})
    (tmp_path / "test/example.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="摘要"):
        read_component(tmp_path, "test")


def test_catalog_aliases_local_models_credentials_and_insecure_legacy_urls(tmp_path):
    from dsh_core_worker.access import Access
    from dsh_core_worker.credentials import ProviderCredentials
    from dsh_core_worker.storage_model_import import import_catalog

    store, access, transfer = (
        Store(tmp_path / "state"),
        Access(tmp_path / "access"),
        Fernet(Fernet.generate_key()),
    )
    owner, source, model = [str(uuid4()) for _ in range(3)]
    provider = "12345678-1234-4234-8234-123456789abc"
    local = "87654321-1234-4234-8234-123456789abc"

    def row(identity, kind, url, key=None):
        return {
            "id": identity,
            "owner_user_id": owner,
            "name": "Synthetic",
            "kind": kind,
            "base_url": url,
            "enabled": 1,
            "supplier_id": None,
            "api_key_ciphertext": key,
        }

    rows = {
        "model_providers": [
            row(provider, "openai", "http://192.0.2.1", transfer.encrypt(b"isolated-key").decode()),
            row(local, "local_qwen_tts", ""),
        ],
        "model_entries": [
            {
                "id": model,
                "provider_id": provider,
                "slug": "fixture-model",
                "label": "Fixture",
                "capabilities": '["chat"]',
                "categories": "[]",
                "enabled": 1,
            }
        ],
    }
    aliases = {}
    catalog, _ = import_catalog(store, access, rows, owner, source, transfer, aliases)
    first = catalog.saved()
    assert first["providers"][0]["base_url"] == "https://migration-disabled.invalid"
    assert not first["providers"][0]["enabled"]
    assert first["providers"][0]["legacy_base_url"] == "http://192.0.2.1"
    assert aliases[provider].startswith("legacy_")
    assert (
        ProviderCredentials(store, access.cipher).secret(
            "PROVIDER_" + aliases[provider].replace("-", "_")
        )
        == "isolated-key"
    )
    catalog, _ = import_catalog(store, access, rows, owner, source, transfer, aliases)
    assert catalog.saved()["providers"] == first["providers"]
    store.close()


def test_model_supplement_requires_quiescence_and_preserves_verified_files(tmp_path):
    from dsh_core_worker.storage_local_models import import_files

    root, bundle = tmp_path / "core", tmp_path / "models"
    source_id = str(uuid4())
    parts = {}
    for part, filename in [
        ("local-models", "fixture/weights.bin"),
        ("local-downloads", "verified-archive.tar.gz"),
    ]:
        path = bundle / part / "files" / filename
        path.parent.mkdir(parents=True)
        path.write_bytes(b"verified fixture")
        info = {"bytes": path.stat().st_size, "sha256": digest(path)}
        parts[part] = component(bundle, part, {}, {filename: info})
    (bundle / "manifest.json").write_text(
        json.dumps(
            {"version": 1, "service": "local-models", "source_id": source_id, "components": parts}
        )
    )
    receipt = tmp_path / "receipt.json"
    with pytest.raises(ValueError, match="已停止"):
        import_files(root, bundle, receipt)
    root.mkdir()
    (root / ".storage-paused").write_text("maintenance")
    first = import_files(root, bundle, receipt)
    assert first == import_files(root, bundle, receipt)
    assert (
        root / "state/local-models/models/fixture/weights.bin"
    ).read_bytes() == b"verified fixture"
    assert json.loads(receipt.read_text())["completed"]
