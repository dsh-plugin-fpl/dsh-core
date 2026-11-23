"""One profile's configuration, execution ownership and recoverable task queue."""

import asyncio
import base64
import fcntl
import json
import shutil
import traceback
from collections import deque
from pathlib import Path

from pydantic import ValidationError

from .agents import Agents
from .contracts import (
    AGENTS,
    CAPABILITIES,
    PROVIDERS,
    AgentConnection,
    Connection,
    CoreError,
    Execution,
)
from .local_models import LocalModels
from .models import PROVIDER_CAPABILITIES, Models
from .resources import Resources
from .store import TERMINAL, Store


class Runtime:
    def __init__(self, root, host, notify):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (root / "worker.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise CoreError("此配置已有执行进程", "profile_busy") from None
        self.store = Store(root)
        self.resources = Resources(self.store)
        self.models = Models(self.store, self.resources, host)
        self.agents = Agents(self.store, host)
        self.local = LocalModels(self.store, self.resources)
        self.notify = notify
        self.host = host
        self.running = {}
        self.gate = asyncio.Semaphore(4)
        self.voice_events = {}
        self.closing = False
        self.store.recover()
        for task in self.store.tasks(active=True):
            if task["request"]["kind"] == "integration":
                self.store.update(
                    task["id"],
                    status="interrupted",
                    error={"code": "unknown_result", "message": "集成调用已中断，不会自动重提"},
                )
            elif task["status"] in ("queued", "recoverable"):
                self.schedule(task["id"])

    async def invoke(self, method, params):
        try:
            return await self.dispatch(method, params)
        except ValidationError:
            raise CoreError("请求字段或参数不符合接口要求", "validation") from None
        except (KeyError, TypeError):
            raise CoreError("缺少必要请求字段", "validation") from None

    async def dispatch(self, method, p):
        if method == "voice.events":
            self.store.task(p["id"])
            return [
                e for e in self.voice_events.get(p["id"], []) if e["seq"] > int(p.get("after", 0))
            ]
        if method == "host.event":
            task = self.store.task(p["id"])
            if task["status"] not in ("running", "processing"):
                return {"accepted": False}
            if p["kind"] == "assistant_delta":
                text = task["text"] + p["value"]["text"]
                if len(text) > 2_000_000:
                    raise CoreError("输出超过任务大小限制", "output_limit")
                self.store.update(p["id"], text=text)
            self.store.event(p["id"], p["kind"], p["value"])
            return {"accepted": True}
        if method == "hello":
            if p.get("protocol") != 1:
                raise CoreError("执行层协议版本不匹配", "protocol_version")
            return {
                "protocol": 1,
                "version": "0.1.0",
                "providers": PROVIDERS,
                "capabilities": CAPABILITIES,
                "agents": AGENTS,
            }
        from .storage_admission import paused

        if paused(self.store.root) and method in {
            "config.save",
            "task.submit",
            "task.retry",
            "local.install",
            "local.remove",
            "local.enable",
            "local.manage",
            "task.resume",
            "resource.put",
            "resource.upload.begin",
            "resource.upload.chunk",
            "resource.upload.finish",
            "resource.delete",
        }:
            raise CoreError("存储迁移中，请稍后重试", "storage_migrating")
        if method == "state":
            return {
                "connections": self.store.config("connections", []),
                "defaults": self.store.config("defaults", {}),
                "agents": self.store.config("agents", {}),
                "settings": self.store.config("settings", {}),
                "tasks": self.store.tasks(),
                "resources": self.resources.list(),
                "local": await self.local.status(),
                "provider_capabilities": PROVIDER_CAPABILITIES,
            }
        if method == "config.save":
            key = p["key"]
            value = p["value"]
            if key == "connections":
                value = [Connection.model_validate(c).model_dump() for c in value]
                if len({c["id"] for c in value}) != len(value):
                    raise CoreError("连接 ID 重复")
                for c in value:
                    if any(
                        cap not in PROVIDER_CAPABILITIES[c["kind"]]
                        for m in c["models"]
                        for cap in m["capabilities"]
                    ):
                        raise CoreError("模型能力与供应商类型不匹配")
            elif key == "agents":
                value = {
                    k: AgentConnection.model_validate({**v, "id": k}).model_dump()
                    for k, v in value.items()
                }
            elif key == "defaults":
                if any(k not in CAPABILITIES for k in value):
                    raise CoreError("默认模型能力无效")
                for capability, item in value.items():
                    request = Execution(
                        request_id="validate", application_id="core", capability=capability, **item
                    )
                    await self.resolve(request)
            elif key == "settings":
                if set(value) - {"temporary_upload_url", "task_timeout_seconds"}:
                    raise CoreError("设置字段无效")
                if value.get("temporary_upload_url"):
                    from .extracted.runtime.http.outbound_urls import require_safe_provider_base_url

                    value["temporary_upload_url"] = require_safe_provider_base_url(
                        value["temporary_upload_url"]
                    )
                if not 30 <= int(value.get("task_timeout_seconds", 3600)) <= 86400:
                    raise CoreError("任务超时须为 30–86400 秒")
            else:
                raise CoreError("未知配置项目")
            return self.store.save_config(key, value, p["revision"])
        if method == "agent.detect":
            return await self.agents.detect(p["id"], p.get("models", True))
        if method == "resource.put":
            if len(p["data_base64"]) > 24 * 1024 * 1024:
                raise CoreError("单次上传超过 16 MB", "resource_too_large")
            try:
                data = base64.b64decode(p["data_base64"], validate=True)
            except ValueError:
                raise CoreError("文件编码无效") from None
            return self.resources.put(
                data, p["name"], p["mime_type"], p.get("application_id", "core")
            )
        if method.startswith("resource.upload."):
            return self.resources.upload(method.rsplit(".", 1)[1], p)
        if method == "resource.chunk":
            return self.resources.chunk(p["id"], p.get("offset", 0), p.get("limit", 1024 * 1024))
        if method == "resource.read":
            return self.resources.read(p["id"])
        if method == "task.get":
            return self.store.task(p["id"])
        if method == "task.events":
            self.store.task(p["id"])
            return self.store.events(p["id"], int(p.get("after", 0)))
        if method == "task.submit":
            request = Execution.model_validate(p)
            if request.kind == "integration":
                raise CoreError("请通过集成调用接口提交", "forbidden")
            replay = self.store.replay(request)
            if replay:
                return replay
            fingerprint_body = request.model_dump(mode="json")
            connection = await self.resolve(request)
            self.validate_parameters(request)
            payload = (
                await self.models.prepare(request, connection)
                if request.kind == "model" and connection["kind"] != "harness"
                else None
            )
            snapshot = {**connection}
            if payload is not None:
                snapshot["payload"] = payload
            task, created = self.store.admit(request, snapshot, fingerprint_body=fingerprint_body)
            if created:
                self.schedule(task["id"])
            return task
        if method == "local.manage":
            action = p["action"]
            if action not in ("install", "repair", "enable", "stop", "uninstall"):
                raise CoreError("未知本地模型操作")
            from .extracted.config.local_models import definition

            definition(p["model_id"])
            request = Execution(
                request_id=p["request_id"],
                application_id="core",
                kind="local",
                model=p["model_id"],
                parameters={"action": action},
            )
            task, created = self.store.admit(request, {"kind": "local_management"})
            if created:
                self.schedule(task["id"])
            return task
        if method == "task.cancel":
            task = self.store.task(p["id"])
            if task["status"] in TERMINAL:
                return task
            task = self.store.update(
                p["id"], status="cancelled", error={"code": "cancelled", "message": "任务已取消"}
            )
            running = self.running.get(p["id"])
            if running:
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
            await self.notify("task.changed", {"id": p["id"]})
            return task
        if method == "task.resume":
            task = self.store.task(p["id"])
            local = (
                task["request"]["kind"] == "local"
                and task["connection"].get("kind") != "local_management"
            )
            if not task["provider_task_id"] and not task["result_url"] and not local:
                raise CoreError("该任务没有可恢复的供应商回执", "not_recoverable")
            if p["id"] not in self.running:
                self.store.update(p["id"], status="queued" if local else "recoverable", error=None)
                self.schedule(p["id"])
            return self.store.task(p["id"])
        if method == "voice.command":
            return await self.models.voice_command(p["id"], p["command"], p.get("value", ""))
        raise CoreError("未知接口方法", "method_not_found")

    def validate_parameters(self, request):
        allowed = {
            "chat": {"system", "temperature", "max_tokens"},
            "vision": {"system", "temperature", "max_tokens"},
            "tools": {"system", "temperature", "max_tokens"},
            "speech_recognition": {
                "language",
                "target_language",
                "enable_word_timestamps",
                "enable_text_edit",
                "enable_speaker_diarization",
                "max_speakers",
                "enable_punctuation",
                "hotwords",
                "abbreviations",
            },
            "voice": {"voice"},
            "video_enhancement": set(),
            "speech_synthesis": {
                "language",
                "reference_text",
                "clone_mode",
                "emotion",
                "emotion_strength",
                "seed",
            },
        }
        if request.kind == "agent":
            allowed_keys = {"reasoning_effort", "service_tier", "model_variant", "harness_effort"}
        elif request.capability in allowed:
            allowed_keys = allowed[request.capability]
        else:
            from .extracted.contracts.model_service.generation_tasks import (
                AudioGenerationTaskCreateIn,
                ImageGenerationTaskCreateIn,
                VideoGenerationTaskCreateIn,
            )

            schema = {
                "image_generation": ImageGenerationTaskCreateIn,
                "video_generation": VideoGenerationTaskCreateIn,
                "audio_generation": AudioGenerationTaskCreateIn,
            }[request.capability]
            allowed_keys = set(schema.model_fields) - {
                "kind",
                "model_id",
                "prompt",
                "canvas_node_id",
                "expected_canvas_revision",
            }
        if set(request.parameters) - allowed_keys:
            raise CoreError("此能力不支持所填的高级参数", "unsupported_parameter")
        if request.capability in ("chat", "vision", "tools") and request.kind != "agent":
            temperature = float(request.parameters.get("temperature", 0.7))
            tokens = int(request.parameters.get("max_tokens", 4096))
            if not 0 <= temperature <= 2 or not 1 <= tokens <= 131072:
                raise CoreError("温度或输出长度超出支持范围")
        if request.kind == "local" and request.capability == "speech_synthesis":
            from .extracted.contracts.model_service.schemas.local_models import (
                SpeechSynthesisParameters,
                ZonosSynthesisParameters,
            )

            if len(request.resource_ids) != 1:
                raise CoreError("请选择一段参考音频")
            self.models.reference(request, request.resource_ids[0], "audio")
            schema = (
                ZonosSynthesisParameters
                if request.model == "zonos3-mini"
                else SpeechSynthesisParameters
            )
            schema.model_validate(
                {
                    "text": request.prompt,
                    "reference_asset_id": request.resource_ids[0],
                    **request.parameters,
                }
            )
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError

        try:
            for tool in request.tools:
                Draft202012Validator.check_schema(tool["inputSchema"])
        except SchemaError:
            raise CoreError("工具参数 Schema 无效") from None

    async def resolve(self, request):
        if not request.connection_id and not request.model:
            chosen = self.store.config("defaults", {})["value"].get(request.capability)
            if chosen:
                request.connection_id = chosen.get("connection_id", "")
                request.model = chosen.get("model", "")
                request.kind = chosen.get("kind", "model")
        if request.kind == "agent":
            connection, executable = self.agents.connection(request.connection_id)
            request.model = request.model or connection.model
            return {**connection.model_dump(), "executable": executable}
        if request.kind == "local":
            from .extracted.config.local_models import definition

            model = definition(request.model)
            if model.get("capability", "speech_synthesis") != request.capability:
                raise CoreError("本地模型能力不匹配")
            for identity in request.resource_ids:
                self.models.reference(request, identity)
            return {"kind": "local", "model": request.model}
        if request.connection_id.startswith("harness:"):
            if request.capability not in ("chat", "vision"):
                raise CoreError("Harness 模型接入支持对话和图片理解")
            return {"kind": "harness", "id": request.connection_id}
        connection = next(
            (
                c
                for c in self.store.config("connections", [])["value"]
                if c["id"] == request.connection_id
            ),
            None,
        )
        if not connection or not connection["enabled"]:
            raise CoreError("请选择已启用的模型连接", "connection_unavailable")
        model = next(
            (m for m in connection["models"] if m["id"] == request.model and m["enabled"]), None
        )
        if not model or request.capability not in model["capabilities"]:
            raise CoreError("请选择支持此能力的模型", "model_unavailable")
        if connection["kind"].startswith("local_"):
            request.kind = "local"
            return await self.resolve(request)
        return connection

    def schedule(self, identity):
        if self.closing or identity in self.running:
            return
        run = asyncio.create_task(self.execute(identity))
        self.running[identity] = run
        run.add_done_callback(lambda _: self.running.pop(identity, None))

    async def execute(self, identity):
        scratch = self.store.root / "tasks" / identity
        try:
            application = self.store.task(identity)["request"]["application_id"]
            if application != "core":
                while not await self.host("application.ready", {"application_id": application}):
                    await asyncio.sleep(1)
            async with self.gate:
                task = self.store.task(identity)
                if task["status"] in TERMINAL:
                    return
                timeout = int(
                    self.store.config("settings", {})["value"].get("task_timeout_seconds", 3600)
                )
                async with asyncio.timeout(timeout):
                    scratch.mkdir(parents=True, mode=0o700, exist_ok=True)
                    request = Execution.model_validate(task["request"])
                    self.store.update(identity, status="running")

                    async def emit(kind, value):
                        if kind == "voice" and value.get("type") == "audio":
                            buffer = self.voice_events.setdefault(identity, deque(maxlen=256))
                            buffer.append(
                                {"seq": buffer[-1]["seq"] + 1 if buffer else 1, "value": value}
                            )
                            return
                        current = self.store.task(identity)
                        if kind == "assistant_delta":
                            text = current["text"] + value["text"]
                            if len(text) > 2_000_000:
                                raise CoreError("输出超过任务大小限制", "output_limit")
                            self.store.update(identity, text=text)
                        if kind == "progress":
                            self.store.update(identity, progress=value.get("progress", 0))
                        self.store.event(identity, kind, value)
                        await self.notify(
                            "task.event", {"id": identity, "kind": kind, "value": value}
                        )

                    if task["connection"].get("kind") == "local_management":
                        result = await self.local.manage(
                            request.model, request.parameters["action"], emit
                        )
                    elif request.kind == "local":
                        result = await self.local.execute(request, emit, scratch)
                    elif request.kind == "agent":
                        result = await self.agents.execute(
                            request, emit, scratch, connection=task["connection"]
                        )
                    else:
                        result = await self.models.execute(
                            task, request, task["connection"], emit, scratch
                        )
                    self.store.update(identity, status="completed", progress=100, result=result)
        except asyncio.CancelledError:
            if self.store.task(identity)["status"] not in ("cancelled", "queued"):
                task = self.store.task(identity)
                self.store.update(
                    identity,
                    status="recoverable"
                    if task["provider_task_id"] or task["result_url"]
                    else "interrupted",
                    error={"code": "worker_stopped", "message": "执行进程已停止"},
                )
        except Exception as error:  # noqa: BLE001 — task boundary records a sanitized failure
            diagnostic = {
                "task_id": identity,
                "type": type(error).__name__,
                "frames": [
                    {"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
                    for f in traceback.extract_tb(error.__traceback__)
                ],
            }
            path = self.store.root / "diagnostics.jsonl"
            if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
                path.replace(path.with_suffix(".previous.jsonl"))
            with path.open("a") as output:
                output.write(json.dumps(diagnostic) + "\n")
            path.chmod(0o600)
            code = getattr(error, "code", "execution_failed")
            message = (
                str(error)
                if isinstance(error, CoreError)
                else "执行失败，请检查连接、参数或运行环境"
            )
            if isinstance(error, TimeoutError):
                code, message = "timeout", "任务已达到执行时限"
            self.store.update(
                identity,
                status="interrupted" if code == "bridge_disconnected" else "failed",
                error={"code": code, "message": message},
            )
        finally:
            if scratch.exists():
                await asyncio.to_thread(shutil.rmtree, scratch)
            await self.notify("task.changed", {"id": identity})

    async def close(self):
        self.closing = True
        pending = list(self.running.values())
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await self.local.close()
        self.store.close()
        self.lock.close()
