"""Recreate managed interpreters using the owning installer's verified archives."""

import asyncio

from .extracted.config.local_models import definition
from .extracted.host.local_models.installer import Installer
from .store import Store


def restore(core_root):
    async def execute():
        store = Store(core_root / "state")
        try:
            enabled = store.config("local", {"enabled": []})["value"].get("enabled", [])
            installer = Installer(store.root / "local-models")

            async def report(_progress):
                pass

            for identity in enabled:
                model = definition(identity)
                if not installer.is_installed(model):
                    await installer.install(model, report, repair=True)
        finally:
            store.close()

    asyncio.run(execute())


def import_files(core_root, bundle, receipt):
    """Explicit supplementary import while the target Core is quiesced."""
    import hashlib
    import json
    from pathlib import Path

    from .private_files import atomic_private
    from .storage_records import copy_component_files, read_component

    core_root, bundle, receipt = map(Path, [core_root, bundle, receipt])
    if not (core_root / ".storage-paused").is_file() or (core_root / "service.json").exists():
        raise ValueError("模型文件导入需要已停止并暂停写入的 Core")
    header = json.loads((bundle / "manifest.json").read_text())
    if (
        header.get("version") != 1
        or header.get("service") != "local-models"
        or set(header.get("components", {})) != {"local-models", "local-downloads"}
    ):
        raise ValueError("模型补充快照格式无效")
    signature = hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()
    saved = (
        json.loads(receipt.read_text())
        if receipt.exists()
        else {
            "version": 1,
            "bundle_sha256": signature,
            "source_id": header["source_id"],
            "completed": False,
        }
    )
    if saved["bundle_sha256"] != signature:
        raise ValueError("补充快照与回执不一致")
    atomic_private(receipt, json.dumps(saved).encode())
    results = {}
    for component, directory in [("local-models", "models"), ("local-downloads", "downloads")]:
        manifest, rows = read_component(bundle, component)
        if rows:
            raise ValueError("模型补充快照不能包含数据库记录")
        results[component] = copy_component_files(
            bundle, component, core_root / "state/local-models" / directory, manifest, {}
        )
    saved.update(completed=True, results=results)
    atomic_private(receipt, json.dumps(saved).encode())
    return {"imported": True, "results": results}
