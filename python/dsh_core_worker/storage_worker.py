"""Durable cutover; no user writes are admitted before the commit checkpoint."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

from .private_files import atomic_private
from .storage_io import copy_tree, validate_target
from .storage_manager import StorageManager


def run(repository, source, identity):
    import fcntl

    manager = StorageManager(repository, source)
    plan = manager.read("plans/" + identity + ".json")
    with (manager.control / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        job = manager.job(identity)
        target = Path(plan["root"])
        control = target / ".migration" / identity
        locator = Path(plan["locator"])
        entries = plan["entries"]
        journal = manager.control / (identity + "-cutover.json")
        state = (
            json.loads(journal.read_text())
            if journal.exists()
            else {
                "previous_locator": locator.read_text() if locator.exists() else None,
                "activated": False,
                "committed": False,
                "copies": {},
            }
        )

        def save():
            atomic_private(journal, json.dumps(state).encode())

        def phase(name, message):
            if manager.read("cancel/" + identity + ".json") and not state["committed"]:
                raise InterruptedError("已取消迁移")
            job.update(
                status="running",
                phase=name,
                message=message,
                updated_at=time.time(),
                pid=os.getpid(),
            )
            manager.save("jobs/" + identity + ".json", job)

        def dest(entry):
            return target / ("core" if entry["id"] == "core" else "workshops/" + entry["id"])

        def gate(root, enabled):
            path = Path(root) / ".storage-paused"
            if enabled:
                atomic_private(path, json.dumps({"migration": identity}).encode())
            else:
                path.unlink(missing_ok=True)

        def lifecycle(entry, action, root):
            if entry["id"] == "core":
                args = [
                    sys.executable,
                    "-m",
                    "dsh_core_worker.cli",
                    action,
                    "--root",
                    str(root),
                    "--web-root",
                    plan["web_root"],
                    "--port",
                    str(plan["port"]),
                    "--no-open",
                ]
            else:
                args = [
                    "node",
                    str(Path(entry["repository"]) / "scripts/application.mjs"),
                    action,
                    "--state-root",
                    str(root),
                ]
            with (manager.control / (identity + "-services.log")).open("ab") as log:
                os.chmod(log.name, 0o600)
                result = subprocess.run(
                    args,
                    check=False,
                    cwd=repository if entry["id"] == "core" else entry["repository"],
                    stdout=log,
                    stderr=log,
                    timeout=180,
                )
            if result.returncode:
                raise ValueError("服务" + action + "失败：" + entry["id"])

        def healthy():
            for entry in entries:
                port = plan["port"] if entry["id"] == "core" else entry["port"]
                with httpx.Client(trust_env=False, timeout=10) as client:
                    response = client.get(f"http://127.0.0.1:{port}/ready")
                    response.raise_for_status()
                    if entry["id"] != "core" and not response.json().get("core_connected"):
                        raise ValueError("应用尚未恢复 Core 连接：" + entry["id"])

        from .storage_import import pause_source, resume_source

        try:
            if not state["committed"]:
                if not journal.exists():
                    validate_target(target, [entry["root"] for entry in entries])
                    save()
                control.mkdir(parents=True, exist_ok=True, mode=0o700)
                phase("quiescing", "正在正常停止应用，保留所有原数据")
                pause_source(plan, control)
                for entry in entries:
                    gate(entry["root"], True)
                # Resume also stops any partially activated target processes first.
                if state["activated"]:
                    for entry in [*entries[1:], entries[0]]:
                        lifecycle(entry, "stop", dest(entry))
                for entry in [*entries[1:], entries[0]]:
                    lifecycle(entry, "stop", entry["root"])
                from .storage_preflight import check_current

                check_current(manager, entries)
                phase("copying", "正在复制并校验各服务自己的数据")
                for entry in entries:
                    if entry["id"] not in state["copies"]:
                        if entry["id"] == "core":
                            result = copy_tree(
                                entry["root"], dest(entry), control / "core-files.json"
                            )
                        else:
                            result = manager.provider(
                                entry,
                                "storage-export",
                                "--state-root",
                                entry["root"],
                                "--storage-output",
                                dest(entry),
                                "--storage-receipt",
                                control / (entry["id"] + "-files.json"),
                            )
                        state["copies"][entry["id"]] = result
                        save()
                    gate(dest(entry), True)
                    phase("copying", "已校验 " + entry["id"])
                if plan.get("legacy"):
                    phase("importing", "正在导入旧 FPL 的业务、原件和配置")
                    from .storage_import import import_legacy

                    # The complete import receipt is durable before model environment setup.
                    # Interrupted setup resumes from the verified quiesced imported state.
                    if not (control / "import-result.json").exists():
                        import_legacy(plan, manager, control)
                phase("restoring_models", "正在恢复本地模型运行环境")
                from .storage_local_models import restore

                restore(target / "core")
                phase("activating", "数据已校验，正在切换统一存储")
                instance = state.setdefault("instance", str(uuid4()))
                atomic_private(
                    target / ".dsh-fpl-storage.json",
                    json.dumps(
                        {"version": 1, "instance": instance, "migration": identity}
                    ).encode(),
                )
                previous = (
                    json.loads(state["previous_locator"]) if state["previous_locator"] else {}
                )
                relocations = dict(previous.get("relocations", {}))
                for entry in entries[1:]:
                    old = str(Path(entry["root"]) / "plugin.json")
                    new = str(dest(entry) / "plugin.json")
                    relocations = {k: new if v == old else v for k, v in relocations.items()}
                    relocations[old] = new
                state["activated"] = True
                save()  # Written before the atomic locator switch; recovery handles either side.
                atomic_private(
                    locator,
                    json.dumps(
                        {
                            "version": 1,
                            "revision": plan["revision"] + 1,
                            "root": str(target),
                            "instance": instance,
                            "relocations": relocations,
                        }
                    ).encode(),
                )
                for entry in entries:
                    lifecycle(entry, "start", dest(entry))
                phase("verifying", "正在核对新目录服务与 Core 连接")
                healthy()
                resume_source(plan, control)
                state["committed"] = True
                save()  # Beyond here rollback is forbidden: user writes can start.
            else:
                for entry in entries:
                    lifecycle(entry, "start", dest(entry))
                healthy()
                resume_source(plan, control)
            for entry in entries:
                gate(dest(entry), False)
                gate(entry["root"], False)
            for descriptor in manager.participants(refresh=True):
                manager.register(descriptor)
            manager.save("legacy-imported.json", {"migration": identity}) if plan.get(
                "legacy"
            ) else None
            job.update(
                status="completed",
                phase="completed",
                message="统一存储迁移完成，原数据保留",
                results=state["copies"],
                root=str(target),
                updated_at=time.time(),
            )
            manager.save("jobs/" + identity + ".json", job)
        except BaseException as exc:
            if state["committed"]:
                job.update(
                    status="needs_recovery",
                    phase="committed",
                    message="数据已切换，请恢复任务完成服务核验；不会回退新写入",
                    updated_at=time.time(),
                )
                manager.save("jobs/" + identity + ".json", job)
                raise
            recovered = True
            if state["activated"]:
                for entry in [*entries[1:], entries[0]]:
                    try:
                        lifecycle(entry, "stop", dest(entry))
                    except (OSError, ValueError, subprocess.SubprocessError):
                        recovered = False
                if recovered:
                    if state["previous_locator"] is None:
                        locator.unlink(missing_ok=True)
                    else:
                        atomic_private(locator, state["previous_locator"].encode())
            try:
                resume_source(plan, control)
            except (OSError, ValueError, subprocess.SubprocessError):
                recovered = False
            if recovered:
                for entry in entries:
                    try:
                        gate(entry["root"], False)
                        lifecycle(entry, "start", entry["root"])
                    except (OSError, ValueError, subprocess.SubprocessError):
                        recovered = False
            job.update(
                status=("cancelled" if isinstance(exc, InterruptedError) else "rolled_back")
                if recovered
                else "needs_recovery",
                phase="failed",
                message=str(exc),
                updated_at=time.time(),
            )
            manager.save("jobs/" + identity + ".json", job)
            raise


if __name__ == "__main__":
    run(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
