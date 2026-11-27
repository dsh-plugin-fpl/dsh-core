"""Core-owned offline snapshots for an embedded product. Sources are never rewritten."""

import fcntl
import hashlib
import json
import sqlite3
from contextlib import ExitStack
from pathlib import Path

from cryptography.fernet import Fernet

from dsh_core_worker.private_files import atomic_private
from dsh_core_worker.storage_io import copy_tree, digest
from dsh_core_worker.store import encode


def _rotate(root):
    key_file = root / "access/secret.key"
    if not key_file.exists():
        return
    old = Fernet(key_file.read_bytes())
    fresh_key = Fernet.generate_key()
    fresh = Fernet(fresh_key)
    # Rotation occurs only inside unpublished staging. A failed attempt is rebuilt
    # from the immutable export, never resumed against partly rotated ciphertext.
    for path in (root / "access").glob("*.enc"):
        if path.name in {"bridges.enc", "launch.enc", "fpl-website.enc"}:
            path.unlink()
        else:
            atomic_private(path, fresh.encrypt(old.decrypt(path.read_bytes())))
    database = root / "state/core.sqlite3"
    if database.exists():
        with sqlite3.connect(database) as db:
            for identity, value in db.execute(
                "SELECT id,value FROM configs WHERE id LIKE 'credential:%'"
            ).fetchall():
                value = json.loads(value)
                if value:
                    db.execute(
                        "UPDATE configs SET value=? WHERE id=?",
                        (encode(fresh.encrypt(old.decrypt(value.encode())).decode()), identity),
                    )
    integrations = root / "integrations/integrations.sqlite3"
    if integrations.exists():
        with sqlite3.connect(integrations) as db:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                columns = [
                    r[1]
                    for r in db.execute(f'PRAGMA table_info("{table}")')
                    if r[1].endswith("_ciphertext")
                ]
                for column in columns:
                    for rowid, value in db.execute(
                        f'SELECT rowid,"{column}" FROM "{table}"'
                    ).fetchall():
                        if value:
                            rotated = fresh.encrypt(old.decrypt(value.encode())).decode()
                            db.execute(
                                f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?', (rotated, rowid)
                            )
    atomic_private(key_file, fresh_key)


def export_product(source, output, application_id, identity_id, bridges):
    """Export a stopped Core into a private, immutable product snapshot.

    bridges maps account IDs to the old application's bridge IDs, supplied by the
    product's own export. Account IDs and resource/task IDs remain stable.
    """
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or source == output or source in output.parents:
        raise ValueError("Core 导出需要独立的空目标目录")
    if not (source / "identity/identity.db").is_file():
        raise ValueError("来源不是已初始化的 Core 状态目录")
    with sqlite3.connect((source / "identity/identity.db").as_uri() + "?mode=ro", uri=True) as db:
        accounts = db.execute("SELECT id,legacy FROM account").fetchall()
        grants = [
            r[0]
            for r in db.execute(
                "SELECT account_id FROM account_application WHERE application_id=?", (identity_id,)
            )
        ]
    tenant_roots = {
        identity: source if legacy else source / "accounts" / identity
        for identity, legacy in accounts
    }
    with ExitStack() as stack:
        for root in tenant_roots.values():
            lock = root / "state/worker.lock"
            if lock.exists():
                stream = stack.enter_context(lock.open("rb"))
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError("请先正常停止来源 Core，再导出快照") from None
        copied = copy_tree(source, output, output.parent / (output.name + "-copy.json"))
    new_scope = (
        "b-local-" + hashlib.sha256(application_id.encode()).hexdigest()[:24] + "-" + application_id
    )
    for identity, original in tenant_roots.items():
        target = output / original.relative_to(source)
        database = target / "state/core.sqlite3"
        allowed = {"b-" + bridge + "-" + application_id for bridge in bridges.get(identity, [])}
        if database.exists():
            with sqlite3.connect(database) as db:
                for task_id, value in db.execute("SELECT id,value FROM tasks").fetchall():
                    task = json.loads(value)
                    old_scope = task["request"]["application_id"]
                    if old_scope not in allowed and old_scope != "core":
                        db.execute("DELETE FROM events WHERE task_id=?", (task_id,))
                        db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
                        continue
                    if old_scope in allowed:
                        task["request"]["application_id"] = new_scope
                    if task["status"] not in {"completed", "failed", "cancelled", "interrupted"}:
                        task.update(
                            status="interrupted",
                            error={
                                "code": "migration_unknown_result",
                                "message": "迁移历史未自动重执行，请核对原任务回执。",
                            },
                        )
                    request = task["request"]
                    db.execute(
                        "UPDATE tasks SET scope=?,fingerprint=?,status=?,value=? WHERE id=?",
                        (
                            encode([request["application_id"], request["session_id"]]),
                            hashlib.sha256(encode(request).encode()).hexdigest(),
                            task["status"],
                            encode(task),
                            task_id,
                        ),
                    )
                for resource_id, value in db.execute("SELECT id,value FROM resources").fetchall():
                    resource = json.loads(value)
                    if resource["application_id"] in allowed:
                        resource["application_id"] = new_scope
                        db.execute(
                            "UPDATE resources SET value=? WHERE id=?",
                            (encode(resource), resource_id),
                        )
                    elif resource["application_id"] != "core":
                        db.execute("DELETE FROM resources WHERE id=?", (resource_id,))
                db.execute("DELETE FROM configs WHERE id='application-catalog'")
        _rotate(target)
    with sqlite3.connect(output / "identity/identity.db") as db:
        for table in [
            "account_session",
            "account_invite",
            "app_login",
            "account_application",
            "identity_bridge_binding",
            "app_client",
        ]:
            db.execute("DELETE FROM " + table)
    files = {
        str(p.relative_to(output)): digest(p)
        for p in sorted(output.rglob("*"))
        if p.is_file() and not p.name.endswith(("-wal", "-shm"))
    }
    manifest = {
        "version": 1,
        "application_id": application_id,
        "identity_id": identity_id,
        "source_snapshot_hash": copied["sha256"],
        "primary_account": next(identity for identity, legacy in accounts if legacy),
        "accounts": [identity for identity, _ in accounts],
        "authorized_accounts": grants,
        "scope": new_scope,
        "files": files,
    }
    atomic_private(output / "product-snapshot.json", encode(manifest).encode())
    return manifest


def verify_snapshot(root):
    root = Path(root)
    manifest = json.loads((root / "product-snapshot.json").read_text())
    for relative, expected in manifest["files"].items():
        path = root / relative
        if (
            path.is_symlink()
            or root.resolve() not in path.resolve().parents
            or digest(path) != expected
        ):
            raise ValueError("Core 快照校验失败：" + relative)
    return manifest


def restore_models(core_root, *, cache_source=None):
    """Rebuild enabled model runtimes at their final product path using verified caches.

    The caller must stop the owning product. We acquire the same Core worker locks;
    no model inference or generation is submitted by this maintenance operation.
    """
    import subprocess
    from contextlib import ExitStack

    from dsh_core_worker.extracted.config.local_models import definition
    from dsh_core_worker.extracted.host.local_models.installer import Installer
    from dsh_core_worker.storage_local_models import restore
    from dsh_core_worker.store import Store

    core_root = Path(core_root).resolve()
    with ExitStack() as stack:
        roots = [core_root, *sorted((core_root / "accounts").glob("*"))]
        for root in roots:
            state = root / "state"
            if not (state / "core.sqlite3").is_file():
                continue
            lock = stack.enter_context((state / "worker.lock").open("a+b"))
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        results = []
        for root in roots:
            if not (root / "state/core.sqlite3").is_file():
                continue
            if cache_source is not None:
                old = (
                    Path(cache_source)
                    / root.relative_to(core_root)
                    / "state/local-models/downloads/uv-cache"
                )
                new = root / "state/local-models/downloads/uv-cache"
                if old.is_dir() and not new.exists():
                    new.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    subprocess.run(
                        ["/bin/cp", "-cRp", str(old), str(new)], check=True, capture_output=True
                    )
            restore(root)
            store = Store(root / "state")
            try:
                enabled = store.config("local", {"enabled": []})["value"].get("enabled", [])
                installer = Installer(root / "state/local-models")
                installed = [
                    identity for identity in enabled if installer.is_installed(definition(identity))
                ]
                if len(installed) != len(enabled):
                    raise ValueError("Enabled local model runtime verification failed")
                results.append({"enabled": len(enabled), "installed": len(installed)})
            finally:
                store.close()
        return {"restored": True, "accounts": results}


def activate_product(staging, target, backup, *, product_lock):
    """Publish a verified product snapshot while its owner and Core writers are locked."""
    staging, target, backup = (Path(p).resolve() for p in (staging, target, backup))
    if not (staging / ".desktop-migration.json").is_file():
        raise ValueError("A verified provider migration is required")
    if any(
        a == b or a in b.parents or b in a.parents
        for a, b in [(staging, target), (backup, target), (staging, backup)]
    ):
        raise ValueError("Cutover paths must be independent")
    with product_lock, ExitStack() as stack:
        current = target if target.exists() else backup
        if target.exists() and backup.exists():
            raise ValueError("Both current and backup directories already exist")
        if (current / "identity.json").is_file() and (
            json.loads((current / "identity.json").read_text())["id"]
            != json.loads((staging / "identity.json").read_text())["id"]
        ):
            raise ValueError("Product identity mismatch")
        embedded = current / "embedded-core"
        for state in [embedded / "state", *embedded.glob("accounts/*/state")]:
            if (state / "worker.lock").exists():
                lock = stack.enter_context((state / "worker.lock").open("rb"))
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.exists():
            target.rename(backup)
        try:
            staging.rename(target)
        except BaseException:
            if not target.exists() and backup.exists():
                backup.rename(target)
            raise
    return {"activated": True}
