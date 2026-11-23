"""Persistent task projections consumed by the existing FPL test and log pages."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import Request

from .contracts import CoreError
from .conversations import iso


class TaskViews:
    def __init__(self, app, content_response):
        self.app, self.content_response = app, content_response

    @property
    def runtime(self):
        return self.app.state.runtime

    @property
    def catalog(self):
        return self.app.state.catalog

    def routes(self):
        return [
            ("/v1/model-test/generation-options", ["GET"]),
            ("/v1/model-test/generation-assets/backfill-status", ["GET"]),
            ("/v1/model-test/generation-assets/backfill", ["POST"]),
            ("/v1/model-test/generation-assets/cleanup", ["POST"]),
            ("/v1/model-test/generation-tasks", ["GET", "POST"]),
            ("/v1/model-test/generation-tasks/{task_id}", ["DELETE"]),
            ("/v1/model-test/generation-tasks/{task_id}/content", ["GET"]),
            ("/v1/model-test/generation-tasks/{task_id}/retry-materialization", ["POST"]),
            ("/v1/model-test/generation-assets", ["POST"]),
            ("/v1/model-test/generation-assets/{asset_id}", ["DELETE"]),
            ("/v1/model-test/generation-assets/{asset_id}/content", ["GET"]),
            ("/v1/model-test/transcription-options", ["GET"]),
            ("/v1/model-test/transcription-tasks", ["GET", "POST"]),
            ("/v1/model-test/transcription-tasks/upload", ["POST"]),
            ("/v1/model-test/transcription-tasks/{task_id}", ["DELETE"]),
            ("/v1/model-test/transcription-tasks/{task_id}/input-content", ["GET"]),
            ("/v1/model-inference/video-enhancements", ["GET", "POST"]),
            ("/v1/model-inference/video-enhancements/{task_id}/actions", ["POST"]),
            ("/v1/model-inference/options", ["GET"]),
            ("/v1/model-inference/tasks", ["GET", "POST"]),
            ("/v1/model-inference/tasks/{task_id}/content", ["GET"]),
            ("/v1/model-inference/tasks/{task_id}/save-to-library", ["POST"]),
            ("/v1/model-inference/assets", ["POST"]),
            ("/v1/model-inference/assets/import", ["POST"]),
            ("/v1/model-inference/assets/{asset_id}", ["DELETE"]),
            ("/v1/model-inference/assets/{asset_id}/content", ["GET"]),
            ("/v1/llm-call-logs", ["GET"]),
            ("/v1/llm-call-logs/stats", ["GET"]),
            ("/v1/llm-call-logs/batch-delete", ["POST"]),
            ("/v1/llm-call-logs/{log_id}", ["GET", "DELETE"]),
            ("/v1/llm-call-logs/{log_id}/reproduction-bundle", ["GET"]),
            ("/v1/model-entries/{model_id}/test", ["POST"]),
            ("/v1/files", ["GET"]),
            ("/v1/file-folders", ["GET"]),
        ]

    def video_capabilities(self, kind, slug):
        from .extracted.apps.model_service.providers.seedance_parameters import video_capabilities

        configured = bool(
            self.runtime.store.config("settings", {})["value"].get("temporary_upload_url")
        )
        result = video_capabilities(kind, slug, upload_configured=configured)
        return result.model_dump() if result else None

    def assets(self, info):
        return {
            "id": info["id"],
            "filename": info["name"],
            "original_filename": info["name"],
            "mime_type": info["mime_type"],
            "media_kind": info["mime_type"].split("/")[0],
            "byte_size": info["byte_size"],
            "claimed": False,
            "created_at": iso(),
        }

    def projection(self, task):
        request, connection = task["request"], task["connection"]
        outputs = (task["result"] or {}).get("resources", [])
        result = outputs[0] if outputs else {}
        model_id = next(
            (
                m["id"]
                for p in self.catalog.view()["providers"]
                for m in p["models"]
                if p["id"] == request["connection_id"] and m["slug"] == request["model"]
            ),
            None,
        )
        capability = request["capability"]
        kind = {
            "image_generation": "image",
            "video_generation": "video",
            "audio_generation": "music",
            "speech_recognition": "transcription",
        }.get(capability, capability)
        if capability == "audio_generation" and request["parameters"].get("mode") == "sfx":
            kind = "sound_effect"
        status = (
            "failed"
            if task["status"] in ("cancelled", "interrupted")
            else "pending"
            if task["status"] == "recoverable"
            else task["status"]
        )
        if capability in ("image_generation", "video_generation") and status == "running":
            status = "processing"
        return {
            "id": task["id"],
            "model_id": model_id,
            "model_slug": request["model"],
            "provider_id": request["connection_id"],
            "provider_kind": connection.get("kind", ""),
            "provider_name": connection.get("name", ""),
            "kind": kind,
            "status": status,
            "progress": task["progress"],
            "execution_info": None,
            "request_payload": {"prompt": request["prompt"], **request["parameters"]},
            "result": task["result"] or {},
            "result_filename": result.get("name", ""),
            "result_mime_type": result.get("mime_type", ""),
            "result_byte_size": result.get("byte_size", 0),
            "result_available": bool(outputs),
            "can_retry_materialization": bool(task["provider_task_id"] or task["result_url"])
            and task["status"] in ("failed", "cancelled", "recoverable", "interrupted"),
            "error_message": (task["error"] or {}).get("message"),
            "log_id": task["id"],
            "revision": int(task["updated_at"] * 1000),
            "created_at": iso(task["created_at"]),
            "updated_at": iso(task["updated_at"]),
            "completed_at": iso(task["updated_at"]) if task["status"] == "completed" else None,
        }

    def video(self, task):
        value = self.projection(task)
        source = self.runtime.resources.describe(task["request"]["resource_ids"][0])
        outputs = (task["result"] or {}).get("resources", [])
        url = "/v1/resources/" + outputs[0]["id"] + "/content" if outputs else None
        duration = self.runtime.store.config("video-duration:" + task["id"], 0)["value"]
        return {
            "id": task["id"],
            "model_id": value["model_id"],
            "filename": source["name"],
            "status": "interrupted" if task["status"] == "cancelled" else task["status"],
            "completed": int(bool(outputs)),
            "revision": value["revision"],
            "error_message": value["error_message"],
            "segments": [
                {
                    "index": 0,
                    "start_seconds": 0,
                    "end_seconds": duration,
                    "result_url": url,
                    "original_url": "/v1/resources/" + source["id"] + "/content",
                }
            ],
            "result_url": url,
        }

    def transcription(self, task):
        value = self.projection(task)
        request, result = task["request"], task["result"] or {}
        source = self.runtime.resources.describe(request["resource_ids"][0])
        parameters = {
            "language": "auto",
            "target_language": None,
            "enable_punctuation": True,
            "enable_text_edit": False,
            "enable_speaker_diarization": True,
            "enable_word_timestamps": False,
            "max_speakers": None,
            "hotwords": "",
            "abbreviations": False,
            **request["parameters"],
        }
        return {
            **value,
            "language": parameters["language"],
            "parameters": parameters,
            "source": {
                "kind": "upload",
                "source_asset_id": source["id"],
                "filename": source["name"],
                "mime_type": source["mime_type"],
                "byte_size": source["byte_size"],
                "available": True,
            },
            "result_text": task["text"],
            "result_segments": result.get("segments", []),
            "result_detail": result.get("detail")
            or {
                "audio_info": None,
                "words": [],
                "processing_status": "success" if task["status"] == "completed" else "unknown",
                "stages": [],
                "warnings": [],
                "overlap_intervals": [],
            },
            "started_at": value["created_at"],
        }

    def hidden(self, key):
        return self.runtime.store.config(key, [])

    def hide(self, key, identities):
        current = self.hidden(key)
        self.runtime.store.save_config(
            key, list(set(current["value"]) | set(identities)), current["revision"]
        )

    def log(self, task):
        result = task.get("result") or {}
        usage = result.get("usage") or {}
        return {
            "id": task["id"],
            "call_source": task["request"]["application_id"],
            "provider_kind": task["connection"].get("kind", task["request"]["kind"]),
            "model": task["request"]["model"],
            "auth_kind": "session",
            "api_key_id": None,
            "api_key_name": None,
            "api_key_prefix": None,
            "trace_id": task["id"],
            "request_payload": task["request"],
            "response_payload": {"text": task["text"], "result": result},
            "prompt_tokens": usage.get("input_tokens", usage.get("prompt_tokens")),
            "completion_tokens": usage.get("output_tokens", usage.get("completion_tokens")),
            "total_tokens": usage.get("total_tokens"),
            "latency_ms": max(0, int((task["updated_at"] - task["created_at"]) * 1000)),
            "status": "success"
            if task["status"] == "completed"
            else "error"
            if task["status"] in ("failed", "interrupted", "cancelled")
            else task["status"],
            "error_message": (task["error"] or {}).get("message"),
            "reproduction_supported": False,
            "reproduction_reason": "保留请求快照，可在测试台核对后重新提交",
            "created_at": iso(task["created_at"]),
        }

    async def handle(self, request: Request):
        path, params, query = request.url.path, request.path_params, request.query_params
        body = (
            await request.json()
            if request.method in ("POST", "PATCH")
            and "application/json" in request.headers.get("content-type", "")
            else {}
        )
        runtime = self.runtime
        if "video-enhancements" in path:
            if request.method == "GET":
                rows = [
                    t
                    for t in runtime.store.tasks()
                    if t["request"]["capability"] == "video_enhancement"
                ]
                values = [self.video(t) for t in rows]
                return [
                    v
                    for v in values
                    if not query.get("model_id") or v["model_id"] == query["model_id"]
                ]
            if params.get("task_id"):
                task = runtime.store.task(params["task_id"])
                if (
                    task["request"]["kind"] != "local"
                    or task["request"]["capability"] != "video_enhancement"
                ):
                    raise CoreError("视频任务不存在", "not_found")
                if int(task["updated_at"] * 1000) != body["expected_revision"]:
                    raise CoreError("任务状态已更新，请刷新", "revision_conflict")
                await runtime.invoke(
                    "task.cancel" if body["action"] == "pause" else "task.resume",
                    {"id": task["id"]},
                )
                return self.video(runtime.store.task(task["id"]))
            task = await runtime.invoke(
                "task.submit",
                self.catalog.execution(
                    body["model_id"],
                    request_id=body["request_id"],
                    application_id="core",
                    session_id="video-enhancement",
                    capability="video_enhancement",
                    resource_ids=[body["source_id"]],
                ),
            )
            import shutil

            from .local_models import process

            if shutil.which("ffprobe"):
                source, _info = runtime.resources.path(body["source_id"])
                try:
                    result = await process(
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "json",
                        source,
                        timeout=20,
                    )
                    duration = float(json.loads(result)["format"]["duration"])
                    current = runtime.store.config("video-duration:" + task["id"])
                    runtime.store.save_config(
                        "video-duration:" + task["id"], duration, current["revision"]
                    )
                except (CoreError, KeyError, ValueError):
                    pass
            return self.video(task)
        if path == "/v1/file-folders":
            return {"items": []}  # Core's resource collection has no folder hierarchy.
        if path == "/v1/files":
            rows = [self.assets(x) for x in runtime.resources.list()]
            rows = [
                x
                for x in rows
                if (not query.get("media_kind") or x["media_kind"] == query["media_kind"])
                and query.get("query", "").lower() in x["filename"].lower()
            ]
            return {"items": rows, "total": len(rows)}
        if "llm-call-logs" in path:
            identity = params.get("log_id")
            if request.method == "DELETE" or path.endswith("/batch-delete"):
                identities = [identity] if identity else body["ids"]
                for item in identities:
                    runtime.store.task(item)
                self.hide("hidden_logs", identities)
                return {"deleted": len(identities)}
            if identity:
                log = self.log(runtime.store.task(identity))
                if path.endswith("/reproduction-bundle"):
                    from fastapi.responses import Response

                    return Response(
                        json.dumps(log, ensure_ascii=False),
                        media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="core-call.json"'},
                    )
                return log
            values = [
                self.log(t)
                for t in runtime.store.tasks()
                if t["id"] not in self.hidden("hidden_logs")["value"]
            ]
            if path.endswith("/stats"):
                today = iso()[:10]
                rows = [v for v in values if v["created_at"].startswith(today)]
                return {
                    "calls_today": len(rows),
                    "total_tokens_today": sum(v["total_tokens"] or 0 for v in rows),
                    "avg_latency_ms_today": int(sum(v["latency_ms"] for v in rows) / len(rows))
                    if rows
                    else 0,
                    "errors_today": sum(v["status"] == "error" for v in rows),
                }
            for key in ("call_source", "provider_kind", "model", "status"):
                if query.get(key):
                    values = [v for v in values if v[key] == query[key]]
            if query.get("q"):
                values = [
                    v
                    for v in values
                    if query["q"].lower() in json.dumps(v, ensure_ascii=False).lower()
                ]
            for key, operator in [
                ("from", lambda a, b: a >= b),
                ("to", lambda a, b: a[:10] <= b[:10]),
            ]:
                if query.get(key):
                    values = [v for v in values if operator(v["created_at"], query[key])]
            page, size = (
                max(1, int(query.get("page", 1))),
                min(100, max(1, int(query.get("page_size", 25)))),
            )
            return {
                "items": values[(page - 1) * size : page * size],
                "total": len(values),
                "page": page,
                "page_size": size,
            }
        if path.endswith(("/options", "-options")):
            capability = "speech_recognition" if "transcription" in path else None
            rows = []
            for provider in self.catalog.view()["providers"]:
                if not provider["enabled"]:
                    continue
                for model in provider["models"]:
                    if not model["enabled"]:
                        continue
                    base = {
                        "id": model["id"],
                        "provider_id": provider["id"],
                        "provider_name": provider["name"],
                        "provider_kind": provider["kind"],
                        "slug": model["slug"],
                        "label": model["label"],
                        "description": model["description"],
                    }
                    if capability and capability in model["capabilities"]:
                        from .extracted.apps.model_service.transcription.parameters import (
                            parameter_capabilities,
                        )

                        rows.append(
                            {
                                **base,
                                "max_input_bytes": 100 * 1024 * 1024,
                                "parameter_capabilities": parameter_capabilities(
                                    SimpleNamespace(**model), SimpleNamespace(**provider)
                                ).model_dump(),
                            }
                        )
                    elif "generation-options" in path:
                        for cap, kind in [
                            ("image_generation", "image"),
                            ("video_generation", "video"),
                        ]:
                            if cap in model["capabilities"]:
                                rows.append(
                                    {
                                        **base,
                                        "kind": kind,
                                        "sizes": ["1024x1024", "1536x1024", "1024x1536"],
                                        "video_capabilities": self.video_capabilities(
                                            provider["kind"], model["slug"]
                                        ),
                                        "workflow_capabilities": None,
                                    }
                                )
                    elif "model-inference" in path:
                        from .extracted.config.local_models import LANGUAGES

                        rows.append(
                            {
                                **base,
                                "modalities": model["capabilities"],
                                "parameters": {
                                    "speech_synthesis": {
                                        "languages": list(LANGUAGES),
                                        "text_max_chars": 2000,
                                        "reference_text_max_chars": 2000,
                                        "reference_max_bytes": 45 * 1024 * 1024,
                                        "reference_formats": ["wav", "mp3"],
                                        "default_language": "Chinese",
                                    }
                                },
                            }
                        )
            result = {"items": rows}
            if capability:
                result.update(
                    max_input_bytes=100 * 1024 * 1024,
                    accepted_extensions=[".wav", ".mp3", ".m4a", ".flac", ".ogg"],
                    accepted_mime_types=[
                        "audio/wav",
                        "audio/mpeg",
                        "audio/mp4",
                        "audio/flac",
                        "audio/ogg",
                    ],
                )
            return result
        if path.endswith("/backfill-status"):
            return {"pending_tasks": 0, "eligible_tasks": 0, "active_tasks": 0, "missing_files": 0}
        if path.endswith("/backfill"):
            return {
                "processed_tasks": 0,
                "copied_assets": 0,
                "skipped_active": 0,
                "skipped_missing": 0,
                "conflicts": 0,
                "remaining_tasks": 0,
            }
        if path.endswith("/cleanup"):
            return {"deleted_assets": 0, "deleted_orphan_files": 0}
        if "assets" in path:
            identity = params.get("asset_id")
            if path.endswith("/content"):
                return self.content_response(identity)
            if request.method == "DELETE":
                runtime.resources.describe(identity)
                self.hide("hidden_resources", [identity])
                return {"deleted": True}
            if path.endswith("/import"):
                return self.assets(runtime.resources.describe(body["asset_id"]))
            form = await request.form()
            rows = []
            for upload in form.values():
                if hasattr(upload, "read"):
                    try:
                        data = await upload.read(runtime.resources.max_bytes + 1)
                        rows.append(
                            self.assets(
                                runtime.resources.put(
                                    data,
                                    upload.filename,
                                    upload.content_type or "application/octet-stream",
                                )
                            )
                        )
                    finally:
                        await upload.close()
            if "model-inference" in path:
                if not rows:
                    raise CoreError("请选择文件")
                return rows[0]
            return {"items": rows}
        identity = params.get("task_id")
        if identity:
            task = runtime.store.task(identity)
            if request.method == "DELETE":
                if task["status"] in ("running", "processing", "queued"):
                    raise CoreError("请先停止任务", "task_busy")
                if int(query.get("expected_revision", int(task["updated_at"] * 1000))) != int(
                    task["updated_at"] * 1000
                ):
                    raise CoreError("任务已更新，请刷新", "revision_conflict")
                self.hide("hidden_tasks", [identity])
                return {"deleted": True}
            if path.endswith("/retry-materialization"):
                await runtime.invoke("task.resume", {"id": identity})
                return self.projection(runtime.store.task(identity))
            if path.endswith("/input-content"):
                return self.content_response(task["request"]["resource_ids"][0])
            outputs = (task["result"] or {}).get("resources", [])
            if not outputs:
                raise CoreError("结果尚未生成", "not_found")
            if path.endswith("/save-to-library"):
                return self.assets(outputs[0])
            return self.content_response(outputs[0]["id"])
        if request.method == "GET":
            caps = (
                ("image_generation", "video_generation")
                if "generation-tasks" in path
                else ("speech_recognition",)
                if "transcription-tasks" in path
                else ("speech_synthesis", "audio_generation", "video_enhancement")
            )
            rows = [
                t
                for t in runtime.store.tasks()
                if t["request"]["capability"] in caps
                and t["id"] not in self.hidden("hidden_tasks")["value"]
            ]
            values = [
                self.transcription(t) if "transcription-tasks" in path else self.projection(t)
                for t in rows
            ]
            if query.get("model_id"):
                values = [v for v in values if v["model_id"] == query["model_id"]]
            return {"items": values, "total": len(values)}
        if path.endswith("/upload"):
            form = await request.form()
            upload = form.get("upload") or form.get("file")
            try:
                resource = runtime.resources.put(
                    await upload.read(runtime.resources.max_bytes + 1),
                    upload.filename,
                    upload.content_type or "audio/wav",
                )
            finally:
                await upload.close()
            body = dict(form)
            body["source_asset_id"] = resource["id"]
            for key in ("parameters",):
                if isinstance(body.get(key), str):
                    body.update(json.loads(body[key]))
        model_id = params.get("model_id") or body["model_id"]
        provider, model = self.catalog.find(model_id)
        if path.endswith("/test"):
            capability, prompt, parameters, references = "chat", "请简短回复连接正常", {}, []
        elif "transcription-tasks" in path:
            capability, prompt = "speech_recognition", ""
            parameters = {
                k: v
                for k, v in body.items()
                if k not in ("model_id", "source_asset_id", "upload", "file", "parameters")
            }
            from .extracted.apps.model_service.transcription.parameters import resolve_parameters
            from .extracted.contracts.model_service.transcription import (
                ModelTestTranscriptionParametersIn,
            )

            parameters = resolve_parameters(
                ModelTestTranscriptionParametersIn.model_validate(parameters),
                SimpleNamespace(**model),
                SimpleNamespace(**provider),
            ).model_dump()
            references = [body["source_asset_id"]]
        elif "generation-tasks" in path:
            capability = "image_generation" if body["kind"] == "image" else "video_generation"
            prompt = body.get("prompt", "")
            parameters = {k: v for k, v in body.items() if k not in ("kind", "model_id", "prompt")}
            references = body.get("reference_asset_ids", [])
        else:
            capability = {
                "image": "image_generation",
                "video": "video_generation",
                "transcription": "speech_recognition",
                "speech_synthesis": "speech_synthesis",
                "music": "audio_generation",
                "sound_effect": "audio_generation",
                "video_enhancement": "video_enhancement",
            }[body["kind"]]
            parameters = body.get("parameters", {})
            prompt = parameters.pop("text", parameters.pop("prompt", ""))
            if body["kind"] == "music":
                parameters["mode"] = "music"
            if body["kind"] == "sound_effect":
                parameters["mode"] = "sfx"
            references = body.get("asset_ids", [])
        if capability not in model["capabilities"]:
            raise CoreError("该模型不支持所选测试方式")
        task = await runtime.invoke(
            "task.submit",
            self.catalog.execution(
                model_id,
                application_id="core",
                session_id="model-test",
                request_id=body.get("request_id") or str(uuid4()),
                capability=capability,
                prompt=prompt,
                parameters=parameters,
                resource_ids=references,
            ),
        )
        if path.endswith("/test"):
            async with asyncio.timeout(120):
                while runtime.store.task(task["id"])["status"] not in (
                    "completed",
                    "failed",
                    "interrupted",
                    "cancelled",
                ):
                    await asyncio.sleep(0.1)
            value = runtime.store.task(task["id"])
            return {
                "ok": value["status"] == "completed",
                "output": value["text"],
                "log_id": value["id"],
                "latency_ms": int((value["updated_at"] - value["created_at"]) * 1000),
            }
        return (
            self.transcription(task)
            if capability == "speech_recognition"
            else self.projection(task)
        )
