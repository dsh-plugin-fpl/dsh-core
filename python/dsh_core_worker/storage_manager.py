"""Machine-owned storage plans and restart-independent migration jobs."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from .private_files import atomic_private
from .storage_io import inventory, validate_target
from .storage_location import DEFAULT_ROOT, locator_path, read_location


class StorageManager:
    def __init__(self, repository, current_root):
        self.repository = Path(repository).resolve()
        self.current_root = Path(current_root).resolve()
        self.control = Path(
            os.environ.get("DSH_FPL_STORAGE_CONTROL", self.repository / ".runtime/storage-control")
        )

    def read(self, name, default=None):
        path = self.control / name
        return json.loads(path.read_text()) if path.exists() else default

    def save(self, name, value):
        atomic_private(
            self.control / name, json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        )

    def register(self, descriptor):
        if descriptor.get("version") != 1 or not re.fullmatch(
            r"dsh-[a-z0-9-]+", descriptor.get("id", "")
        ):
            raise ValueError("应用存储声明无效")
        for field in ["repository", "python", "root"]:
            if not Path(descriptor[field]).is_absolute():
                raise ValueError("应用存储声明必须使用绝对路径")
        if not re.fullmatch(r"fpl_[a-z]+", descriptor.get("module", "")):
            raise ValueError("应用维护模块无效")
        participants = self.read("participants.json", {})
        participants[descriptor["id"]] = descriptor
        self.save("participants.json", participants)

    def participants(self, refresh=False):
        result = []
        for descriptor in self.read("participants.json", {}).values():
            if refresh:
                result.append(self.provider(descriptor, "storage-info"))
            else:
                from .storage_location import root_for

                result.append(
                    {**descriptor, "root": str(root_for(descriptor["id"], descriptor["root"]))}
                )
        return sorted(result, key=lambda row: row["id"])

    @staticmethod
    def provider(descriptor, command, *extra):
        args = [
            descriptor["python"],
            "-m",
            descriptor["module"] + ".cli",
            command,
            *map(str, extra),
        ]
        result = subprocess.run(
            args, check=False, cwd=descriptor["repository"], capture_output=True, text=True
        )
        if result.returncode:
            raise ValueError("应用维护入口失败：" + descriptor["id"] + "/" + command)
        return json.loads(result.stdout)

    def status(self):
        location = read_location()
        participants = self.participants()
        jobs = []
        directory = self.control / "jobs"
        if directory.exists():
            jobs = sorted(
                (json.loads(p.read_text()) for p in directory.glob("*.json")),
                key=lambda row: row["created_at"],
                reverse=True,
            )[:10]
        for row in jobs:
            if row["status"] not in {
                "completed",
                "failed",
                "cancelled",
                "rolled_back",
                "needs_recovery",
            } and row.get("pid"):
                try:
                    os.kill(row["pid"], 0)
                except ProcessLookupError:
                    row.update(
                        status="needs_recovery",
                        message="迁移进程已中断，原件和回执保留，请恢复迁移",
                    )
                    self.save("jobs/" + row["id"] + ".json", row)
        return {
            "configured": location is not None,
            "revision": location["revision"] if location else 0,
            "root": location["root"] if location else str(DEFAULT_ROOT),
            "core_root": str(self.current_root),
            "participants": participants,
            "jobs": jobs,
            "legacy_source_configured": (self.control / "legacy-source.json").exists(),
            "legacy_imported": (self.control / "legacy-imported.json").exists(),
        }

    def plan(self, target_root, revision, include_legacy=False):
        location = read_location()
        if revision != (location["revision"] if location else 0):
            raise ValueError("存储配置已变化，请刷新后重试")
        participants = self.participants(refresh=True)
        if location:
            known = {row["id"] for row in participants}
            workshops = Path(location["root"]) / "workshops"
            if workshops.exists() and any(
                path.is_dir() and path.name not in known for path in workshops.iterdir()
            ):
                raise ValueError("发现尚未登记迁移入口的工坊，请先由该工坊登记存储；当前数据未修改")
        sources = [self.current_root, *(Path(row["root"]) for row in participants)]
        target, free = validate_target(target_root, sources)
        entries = [
            {"id": "core", "root": str(self.current_root), **inventory(self.current_root)},
            *participants,
        ]
        size = sum(row["bytes"] for row in entries)
        if include_legacy and self.read("legacy-imported.json"):
            raise ValueError("旧 FPL 已导入；新的来源交接需独立迁移操作，不会覆盖已迁入的数据")
        legacy = self.read("legacy-source.json") if include_legacy else None
        if legacy:
            size += legacy.get("estimated_import_bytes", legacy["bytes"])
        if free < size + max(1024**3, size // 10):
            raise ValueError("目标磁盘空间不足，需同时保留源数据和迁移副本")
        value = {
            "id": str(uuid4()),
            "created_at": time.time(),
            "revision": revision,
            "root": str(target),
            "entries": entries,
            "required_bytes": size,
            "available_bytes": free,
            "legacy": legacy,
            "repository": str(self.repository),
            "locator": str(locator_path()),
            "port": self.read_service_port(),
            "web_root": str(self.repository / "web/dist"),
        }
        self.save("plans/" + value["id"] + ".json", value)
        return value

    def read_service_port(self):
        record = self.current_root / "service.json"
        return json.loads(record.read_text()).get("port", 9310) if record.exists() else 9310

    def job(self, identity):
        if not re.fullmatch(r"[0-9a-f-]{36}", identity):
            raise ValueError("迁移标识无效")
        value = self.read("jobs/" + identity + ".json")
        if value is None:
            raise ValueError("迁移任务不存在")
        return value

    def cancel(self, identity):
        value = self.job(identity)
        if self.read(identity + "-cutover.json", {}).get("committed"):
            raise ValueError("数据已切换，请恢复服务核验，不能取消新目录")
        self.save("cancel/" + identity + ".json", {"requested": time.time()})
        return value

    def start(self, identity, resume=False):
        import fcntl

        self.control.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.control / "admission.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if resume and self.job(identity)["status"] in {
                "completed",
                "rolled_back",
                "cancelled",
                "failed",
            }:
                raise ValueError("已结束任务不可恢复，请重新预检以包含当前数据")
            for row in self.status()["jobs"]:
                if row["id"] == identity and resume:
                    pid = row.get("pid")
                    if pid:
                        try:
                            os.kill(pid, 0)
                        except ProcessLookupError:
                            pass
                        else:
                            raise ValueError("迁移进程仍在运行")
                    continue
                if row["status"] not in {"completed", "failed", "cancelled", "rolled_back"}:
                    raise ValueError("已有迁移任务，请先查看或恢复")
            plan = self.read("plans/" + identity + ".json")
            if not plan:
                raise ValueError("迁移预检不存在")
            current = read_location()
            if not resume and plan["revision"] != (current["revision"] if current else 0):
                raise ValueError("存储配置已变化，请重新预检")
            if resume:
                (self.control / "cancel" / (identity + ".json")).unlink(missing_ok=True)
            value = {
                "id": identity,
                "created_at": plan["created_at"],
                "status": "queued",
                "phase": "queued",
                "message": "等待迁移进程",
                "pid": None,
            }
            self.save("jobs/" + identity + ".json", value)
            log = self.control / (identity + ".log")
            with log.open("ab") as output:
                os.chmod(log, 0o600)
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "dsh_core_worker.storage_worker",
                        str(self.repository),
                        str(self.current_root),
                        identity,
                    ],
                    cwd=self.repository,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=output,
                    start_new_session=True,
                )
            value = self.read("jobs/" + identity + ".json", value)
            value["pid"] = child.pid
            self.save("jobs/" + identity + ".json", value)
            return value
