"""Capability dispatch over the extracted provider protocols."""

import asyncio
import base64
from dataclasses import asdict
from types import SimpleNamespace

from pydantic import TypeAdapter

from .contracts import CoreError
from .extracted.apps.model_service.generation.driver import ImmediateGenerationResult
from .extracted.apps.model_service.generation.media import download_generated_media
from .extracted.apps.model_service.generation.provider_tasks import (
    poll_generation_task,
    upload_temporary_reference,
)
from .extracted.apps.model_service.generation.visual_payload import prepare_visual_payload
from .extracted.apps.model_service.generation.visual_submission import (
    VisualGenerationInputs,
    submit_visual_generation,
)
from .extracted.apps.model_service.providers import zealman_video
from .extracted.apps.model_service.providers.agent import HttpAgentAdapter
from .extracted.apps.model_service.providers.seedance_references import (
    SeedanceReference,
    validate_seedance_references,
)
from .extracted.apps.model_service.providers.transcription import transcribe_model_audio
from .extracted.contracts.agent_runtime.dialogue import (
    AgentMessage,
    ImageInput,
    ModelTextDelta,
    ModelTurnEnd,
    ModelUsageUpdate,
    ToolSpec,
)
from .extracted.contracts.model_service.generation_tasks import GenerationTaskCreateIn

PROVIDER_CAPABILITIES = {
    "openai": ["chat", "vision", "tools", "speech_recognition"],
    "deepseek": ["chat", "tools"],
    "anthropic": ["chat", "vision", "tools"],
    "volcengine_image": ["image_generation"],
    "volcengine_video": ["video_generation"],
    "zealman_video": ["video_generation"],
    "autodl_video": ["video_generation"],
    "local_qwen_tts": ["speech_synthesis"],
    "local_zonos_tts": ["speech_synthesis"],
    "local_relayvsr": ["video_enhancement"],
    "local_ffmpeg": ["video_enhancement"],
}


class Models:
    def __init__(self, store, resources, host):
        self.store, self.resources, self.host = store, resources, host

    def reference(self, request, identity, kind=None, accepted_mimes=None):
        path, info = self.resources.path(identity)
        if info["application_id"] not in (request.application_id, "core"):
            raise CoreError("此应用未持有该资源", "resource_scope")
        media_kind = info["mime_type"].split("/")[0]
        if (
            kind
            and media_kind != kind
            or accepted_mimes
            and info["mime_type"] not in accepted_mimes
        ):
            raise CoreError("参考素材格式不匹配", "invalid_reference")
        return path, SimpleNamespace(**info, media_kind=media_kind, original_filename=info["name"])

    async def prepare(self, request, connection):
        if request.capability not in ("image_generation", "video_generation", "audio_generation"):
            for identity in request.resource_ids:
                self.reference(request, identity)
            return None
        kind = {
            "image_generation": "image",
            "video_generation": "video",
            "audio_generation": "audio",
        }[request.capability]
        data = {
            "kind": kind,
            "model_id": request.model,
            "prompt": request.prompt,
            **request.parameters,
        }
        if request.resource_ids:
            data.setdefault("reference_asset_ids", request.resource_ids)
            if kind == "video":
                data.setdefault("mode", "reference")
        body = TypeAdapter(GenerationTaskCreateIn).validate_python(data)
        upload_url = self.store.config("settings", {})["value"].get("temporary_upload_url", "")

        async def reference(identity, kind, accepted_mimes=None):
            return self.reference(request, identity, kind, accepted_mimes)[1]

        async def validate(slug, payload, references):
            await validate_seedance_references(
                slug,
                payload,
                [
                    SeedanceReference(r.media_kind, r.mime_type, self.resources.path(r.id)[0])
                    for r in references
                ],
                upload_url,
            )

        return await prepare_visual_payload(
            body,
            provider_kind=connection["kind"],
            model_slug=request.model,
            explicit_fields=body.model_fields_set,
            reference_titles={},
            input_snapshot={},
            reference=reference,
            validate_seedance=validate,
            temporary_upload_configured=bool(upload_url),
        )

    async def execute(self, task, request, connection, emit, scratch):
        if connection.get("kind") == "harness":
            return await self.host(
                "harness.generate", {"task_id": task["id"], "request": request.model_dump()}
            )
        key = (
            await self.host("credential.resolve", {"ref": connection["credential_ref"]})
            if connection.get("credential_ref")
            else ""
        )
        args = {
            "provider_kind": connection["kind"],
            "base_url": connection["base_url"],
            "api_key": key,
        }
        if request.capability in ("chat", "vision", "tools"):
            adapter = HttpAgentAdapter(
                kind=connection["kind"], base_url=connection["base_url"], api_key=key
            )
            images = []
            for identity in request.resource_ids:
                path, info = self.reference(request, identity, "image")
                if info.byte_size > 20 * 1024 * 1024:
                    raise CoreError("图片超过 20 MB", "resource_too_large")
                images.append(
                    ImageInput(
                        info.mime_type, base64.b64encode(path.read_bytes()).decode(), info.byte_size
                    )
                )
            messages = []
            for item in request.messages:
                references = []
                for identity in item.resource_ids:
                    path, info = self.reference(request, identity, "image")
                    if info.byte_size > 20 * 1024 * 1024:
                        raise CoreError("图片超过 20 MB", "resource_too_large")
                    references.append(
                        ImageInput(
                            info.mime_type,
                            base64.b64encode(path.read_bytes()).decode(),
                            info.byte_size,
                        )
                    )
                messages.append(AgentMessage(item.role, item.content, tuple(references)))
            if not messages:
                messages = [AgentMessage("user", request.prompt, tuple(images))]
            tools = [
                ToolSpec(t["name"], t.get("description", ""), t.get("inputSchema", {}))
                for t in request.tools
            ]
            for index in range(16):
                text = ""
                end = None
                async for event in adapter.stream_turn(
                    system=str(request.parameters.get("system", "")),
                    messages=messages,
                    tools=tools,
                    model=request.model,
                    temperature=float(request.parameters.get("temperature", 0.7)),
                    max_tokens=int(request.parameters.get("max_tokens", 4096)),
                    round_index=index,
                ):
                    if isinstance(event, ModelTextDelta):
                        text += event.text
                        await emit("assistant_delta", {"text": event.text})
                    elif isinstance(event, ModelUsageUpdate):
                        await emit("usage", asdict(event.usage))
                    elif isinstance(event, ModelTurnEnd):
                        end = event
                if end is None:
                    raise CoreError("模型流未正常结束", "incomplete_stream")
                if not end.tool_calls:
                    return {"usage": asdict(end.usage)}
                messages.append(AgentMessage("assistant", text, tool_calls=end.tool_calls))
                for call in end.tool_calls:
                    if call.name not in {t.name for t in tools}:
                        raise CoreError("模型调用了未注册工具", "tool_unavailable")
                    from jsonschema import validate

                    validate(
                        call.arguments,
                        next(t.parameters_schema for t in tools if t.name == call.name),
                    )
                    result = await self.host(
                        "tool.call",
                        {
                            "application_id": request.application_id,
                            "session_id": request.session_id,
                            "name": call.name,
                            "arguments": call.arguments,
                            "input_schema": next(
                                t.parameters_schema for t in tools if t.name == call.name
                            ),
                        },
                    )
                    messages.append(
                        AgentMessage("tool", str(result), tool_call_id=call.id, tool_name=call.name)
                    )
            raise CoreError("已达到工具调用轮数上限", "tool_round_limit")
        if request.capability == "speech_recognition":
            if len(request.resource_ids) != 1:
                raise CoreError("请选择一个待转写音频")
            path, info = self.reference(request, request.resource_ids[0], "audio")
            with path.open("rb") as source:
                result = await transcribe_model_audio(
                    **args,
                    model=request.model,
                    filename=info.name,
                    source=source,
                    mime_type=info.mime_type,
                    byte_size=info.byte_size,
                    language=request.parameters.get("language", "auto"),
                    parameters=request.parameters,
                )
            await emit("assistant_delta", {"text": result.text})
            return asdict(result)
        if request.capability == "voice":
            raise CoreError("当前没有可用的实时语音供应商", "capability_unavailable", status=409)
        return await self.generate(task, request, args, emit, scratch)

    async def generate(self, task, request, args, emit, scratch):
        async def local(identity, kind, owner):
            path, info = self.reference(request, identity, kind)
            return path, info.mime_type

        async def uri(identity, owner, expected_kind="image"):
            path, info = self.reference(request, identity, expected_kind)
            if info.byte_size > 45 * 1024 * 1024:
                raise CoreError("参考素材超过内联大小限制")
            return f"data:{info.mime_type};base64," + base64.b64encode(path.read_bytes()).decode()

        async def temporary(identity, kind, owner, filename=None):
            path, info = self.reference(request, identity, kind)
            url = self.store.config("settings", {})["value"].get("temporary_upload_url", "")
            if not url:
                raise CoreError("请配置临时素材上传服务", "upload_unconfigured")
            return await upload_temporary_reference(
                path=path, mime_type=info.mime_type, upload_url=url, filename=filename
            )

        if not task["provider_task_id"] and not task["result_url"]:
            result = await submit_visual_generation(
                VisualGenerationInputs(local, uri, temporary),
                **args,
                model_slug=request.model,
                payload=task["connection"]["payload"],
                owner_user_id=request.application_id,
            )
            if isinstance(result, ImmediateGenerationResult):
                task = self.store.update(task["id"], result_url=result.result_url)
            else:
                task = self.store.update(task["id"], provider_task_id=result, status="processing")
        while not task["result_url"]:
            poll = (
                zealman_video.get_task
                if args["provider_kind"] == "zealman_video"
                else poll_generation_task
            )
            query = {**args, "provider_task_id": task["provider_task_id"]}
            if args["provider_kind"] == "zealman_video":
                query.pop("provider_kind")
                query["task_id"] = query.pop("provider_task_id")
            status = await poll(**query)
            await emit("progress", {"progress": status.progress, "upstream_status": status.status})
            if status.status == "failed":
                raise CoreError("供应商生成任务失败", "provider_failed")
            if status.status == "completed":
                if not status.result_url:
                    raise CoreError("供应商未返回生成结果", "missing_result")
                task = self.store.update(task["id"], result_url=status.result_url)
                break
            await asyncio.sleep(3)
        url = task["result_url"]
        output = scratch / "generated"
        if url.startswith("data:"):
            header, encoded = url.split(",", 1)
            if ";base64" not in header or len(encoded) > 350 * 1024 * 1024:
                raise CoreError("生成结果编码无效")
            data = base64.b64decode(encoded, validate=True)
            mime = header[5:].split(";")[0]
        else:
            result = await download_generated_media(
                url=url, target=output, max_bytes=self.resources.max_bytes
            )
            data = output.read_bytes()
            mime = result[2]
        resource = self.resources.put(
            data, "generated." + mime.split("/")[-1], mime, request.application_id
        )
        return {"resources": [resource], "provider_task_id": task["provider_task_id"]}

    async def voice_command(self, identity, command, value):
        raise CoreError("当前没有可用的实时语音供应商", "capability_unavailable", status=409)
