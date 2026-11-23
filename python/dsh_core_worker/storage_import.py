"""Compose public source exporters and application-owned target importers."""

import json
import subprocess
from pathlib import Path

from cryptography.fernet import Fernet

from .private_files import atomic_private
from .storage_core_import import import_bundle


def source_command(source, action, *extra):
    result = subprocess.run(
        [*source["producer"], action, *map(str, extra)],
        check=False,
        cwd=source["repository"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise ValueError("旧 FPL 的公开维护入口失败：" + action)
    return json.loads(result.stdout)


def pause_source(plan, control):
    source = plan.get("legacy")
    if source is None:
        return
    receipt = (
        Path(source["lifecycle_file"])
        if source and source.get("lifecycle_file")
        else control / "source-lifecycle.json"
    )
    if not receipt.exists():
        source_command(source, "pause", "--lifecycle-file", receipt)


def resume_source(plan, control):
    source = plan.get("legacy")
    receipt = (
        Path(source["lifecycle_file"])
        if source and source.get("lifecycle_file")
        else control / "source-lifecycle.json"
    )
    if source and receipt.exists():
        result = source_command(source, "resume", "--lifecycle-file", receipt)
        atomic_private(control / "source-restored.json", json.dumps(result).encode())


def import_legacy(plan, manager, control):
    source = plan["legacy"]
    key = control / "transfer.key"
    if not key.exists():
        atomic_private(
            key,
            Path(source["prepared_key_file"]).read_bytes()
            if source.get("prepared_key_file")
            else Fernet.generate_key(),
        )
    exported = dict(source.get("prepared_exports", {}))
    services = {
        "dsh-visual-workshop": "visual",
        "dsh-video-editor": "video",
        "dsh-presentation-workshop": "presentation",
        "dsh-thought-workshop": "thought",
        "dsh-galgame-workshop": "galgame",
        "dsh-audio-annotation": "annotation",
    }
    for service in ["core", *[services[entry["id"]] for entry in plan["entries"][1:]]]:
        directory = Path(exported.get(service, control / ("source-" + service)))
        if not (directory / "manifest.json").exists():
            # An interrupted, unpublished export never becomes an import input.
            if directory.exists():
                from uuid import uuid4

                directory = control / ("source-" + service + "-" + uuid4().hex)
            source_command(
                source,
                "export",
                "--state-root",
                source["state_root"],
                "--models-root",
                source["models_root"],
                "--service",
                service,
                "--output",
                directory,
                "--config",
                source["config"],
                "--transfer-key-file",
                key,
            )
        exported[service] = str(directory)
    atomic_private(control / "exports.json", json.dumps(exported).encode())
    result = import_bundle(
        Path(plan["root"]) / "core",
        Path(exported["core"]),
        source["primary_owner"],
        key,
        control / "core-import.json",
    )
    owners = control / "owners.json"
    atomic_private(
        owners,
        json.dumps(
            {
                "primary_source": result["primary_source"],
                "accounts": result["accounts"],
                "model_aliases": result["model_aliases"],
            }
        ).encode(),
    )
    results = {"core": result}
    for entry in plan["entries"][1:]:
        destination = Path(plan["root"]) / "workshops" / entry["id"]
        results[entry["id"]] = manager.provider(
            entry,
            "storage-import",
            "--state-root",
            destination,
            "--storage-bundle",
            exported[services[entry["id"]]],
            "--storage-owners",
            owners,
            "--transfer-key-file",
            key,
        )
    atomic_private(control / "import-result.json", json.dumps(results, ensure_ascii=False).encode())
    return results
