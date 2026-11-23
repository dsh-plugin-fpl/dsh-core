"""Local maintenance CLI. Mutations are explicit; descriptors contain no credentials."""

import argparse
import json
from pathlib import Path

from .storage_location import DEFAULT_ROOT, root_for
from .storage_manager import StorageManager


def main(repository):
    parser = argparse.ArgumentParser(description="Core 统一存储维护")
    parser.add_argument(
        "command",
        choices=[
            "storage-status",
            "storage-register",
            "storage-register-source",
            "storage-plan",
            "storage-migrate",
            "storage-job",
            "storage-resume",
            "storage-cancel",
            "storage-export",
            "storage-import",
            "storage-import-models",
        ],
    )
    parser.add_argument("--root", type=Path)
    parser.add_argument("--descriptor", type=Path)
    parser.add_argument("--target-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--revision", type=int, default=0)
    parser.add_argument("--id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--primary-owner")
    parser.add_argument("--transfer-key-file", type=Path)
    parser.add_argument("--include-legacy", action="store_true")
    options = parser.parse_args()
    root = root_for("core", repository / ".runtime/core", options.root)
    manager = StorageManager(repository, root)
    if options.command == "storage-export":
        if not options.output or not options.receipt:
            parser.error("需要输出路径与迁移回执")
        from .storage_io import copy_tree

        result = copy_tree(root, options.output, options.receipt)
    elif options.command == "storage-import-models":
        if not options.bundle or not options.receipt:
            parser.error("需要补充模型快照与回执")
        from .storage_local_models import import_files

        result = import_files(root, options.bundle, options.receipt)
    elif options.command == "storage-import":
        if not all(
            [options.bundle, options.receipt, options.primary_owner, options.transfer_key_file]
        ):
            parser.error("需要迁移包、回执、主要账号与传输密钥")
        from .storage_core_import import import_bundle

        result = import_bundle(
            root, options.bundle, options.primary_owner, options.transfer_key_file, options.receipt
        )
    elif options.command == "storage-register":
        if options.descriptor is None:
            parser.error("需要 --descriptor")
        manager.register(json.loads(options.descriptor.read_text()))
        result = {"registered": True}
    elif options.command == "storage-register-source":
        if options.descriptor is None:
            parser.error("需要 --descriptor")
        descriptor = json.loads(options.descriptor.read_text())
        from .storage_import import source_command

        descriptor.update(
            source_command(
                descriptor,
                "info",
                "--state-root",
                descriptor["state_root"],
                "--models-root",
                descriptor["models_root"],
            )
        )
        manager.save("legacy-source.json", descriptor)
        result = {"registered": True}
    elif options.command == "storage-plan":
        result = manager.plan(str(options.target_root), options.revision, options.include_legacy)
    elif options.command == "storage-migrate":
        if not options.id:
            parser.error("需要预检 --id")
        result = manager.start(options.id)
    elif options.command == "storage-resume":
        result = manager.start(options.id or "", True)
    elif options.command == "storage-cancel":
        result = manager.cancel(options.id or "")
    elif options.command == "storage-job":
        result = manager.job(options.id or "")
    else:
        result = manager.status()
    print(json.dumps(result, ensure_ascii=False))
