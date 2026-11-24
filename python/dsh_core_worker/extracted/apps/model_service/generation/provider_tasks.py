from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from dsh_core_worker.extracted.apps.model_service.generation.driver import ImmediateGenerationResult
from dsh_core_worker.extracted.apps.model_service.generation.media import GenerationMaterializationError
from dsh_core_worker.extracted.apps.model_service.providers import autodl_video, volcengine_video
from dsh_core_worker.extracted.apps.model_service.providers.common import (
    sanitize_provider_message,
)
from dsh_core_worker.extracted.apps.model_service.providers.temporary_media import (
    TemporaryMediaError,
    upload_temporary_media,
)
from dsh_core_worker.extracted.apps.model_service.providers.volcengine_image import generate_image
from dsh_core_worker.extracted.runtime.http.outbound_urls import require_safe_provider_base_url


def safe_generation_error(exc: Exception) -> str:
    """Convert provider/runtime errors to stable, non-sensitive user messages."""
    if isinstance(exc, TemporaryMediaError):
        return "参考素材临时上传失败，请重新生成"
    if isinstance(exc, (GenerationMaterializationError, ValueError)):
        return sanitize_provider_message(str(exc))
    if isinstance(exc, httpx.TimeoutException):
        return "供应商请求超时"
    return f"任务处理失败（{type(exc).__name__}）"


async def submit_generation_task(
    *,
    provider_kind: str,
    base_url: str,
    api_key: str,
    model_slug: str,
    payload: dict[str, Any],
    image_reference: str | None = None,
    image_references: list[str] | None = None,
    video_content: list[dict[str, object]] | None = None,
) -> str | ImmediateGenerationResult:
    """Submit a normalized image or video task."""
    base_url = require_safe_provider_base_url(base_url)
    if provider_kind == "autodl_video":
        return await autodl_video.create_task(
            base_url=base_url,
            api_key=api_key,
            model=model_slug,
            payload=payload,
            content=video_content or [],
        )
    if provider_kind == "volcengine_image":
        return ImmediateGenerationResult(
            await generate_image(
                base_url=base_url,
                api_key=api_key,
                model=model_slug,
                payload=payload,
                references=image_references or ([image_reference] if image_reference else []),
            )
        )

    if provider_kind == "volcengine_video":
        return await volcengine_video.create_task(
            base_url=base_url,
            api_key=api_key,
            model=model_slug,
            payload=payload,
            content=video_content or [],
        )

    raise ValueError("当前供应商不支持该生成任务")


async def poll_generation_task(
    *, provider_kind: str, base_url: str, api_key: str, provider_task_id: str
):
    base_url = require_safe_provider_base_url(base_url)
    if provider_kind == "autodl_video":
        return await autodl_video.get_task(
            base_url=base_url, api_key=api_key, task_id=provider_task_id
        )
    if provider_kind == "volcengine_video":
        return await volcengine_video.get_task(
            base_url=base_url, api_key=api_key, task_id=provider_task_id
        )
    raise ValueError("当前供应商不支持任务查询")


async def upload_temporary_reference(
    *,
    path: Path,
    mime_type: str,
    upload_url: str,
    transport: httpx.AsyncBaseTransport | None = None,
    filename: str | None = None,
    file_extension: str = "",
) -> str:
    return await upload_temporary_media(
        path=path,
        mime_type=mime_type,
        upload_url=upload_url,
        transport=transport,
        filename=filename,
        file_extension=file_extension,
    )
