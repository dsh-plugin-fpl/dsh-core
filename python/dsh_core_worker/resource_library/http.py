"""Public account-scoped resource-library routes; product embedding uses CoreHttp."""

import mimetypes
from contextlib import asynccontextmanager

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..identity_permissions import role_permissions
from ..integrations.contracts.errors import DuplicateIntegrationError, IntegrationValidationError
from .library import join

router = APIRouter(tags=["resource-library"])


class SourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)
    ref: str = Field(default="", max_length=240)
    name: str = Field(default="", max_length=120)


class ResourceImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    resource_key: str = Field(pattern=r"^[0-9a-f]{24}$")
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    names: dict[str, str] = Field(default_factory=dict, max_length=30)
    configuration: dict[str, dict] = Field(default_factory=dict, max_length=30)


def library(request, write=False):
    access = request.app.state.access
    session = access.session(request.cookies.get("core_session", ""))
    if not session:
        raise HTTPException(401, "请先进入本机应用")
    profile = access.profile() if hasattr(access, "profile") else {"role": "admin"}
    if ("configuration.manage" if write else "content.read") not in role_permissions(
        profile["role"]
    ):
        raise HTTPException(403, "当前账号没有此操作权限")
    return request.app.state.resource_library


@asynccontextmanager
async def validation():
    try:
        yield
    except DuplicateIntegrationError:
        raise HTTPException(409, "名称已存在，请修改导入名称后重试") from None
    except (ValueError, KeyError, TypeError, IntegrationValidationError, ValidationError) as error:
        raise HTTPException(422, "资源格式或配置无效：" + str(error)[:500]) from None


@router.get("/v1/resource-sources")
async def sources(request: Request) -> dict:
    return {"items": library(request).sources()}


@router.post("/v1/resource-sources")
async def add_source(request: Request, body: SourceInput) -> dict:
    async with validation():
        return await library(request, True).add_source(body.url, body.ref, body.name)


@router.delete("/v1/resource-sources/{source_id}")
async def remove_source(request: Request, source_id: str) -> dict:
    return await library(request, True).remove_source(source_id)


@router.get("/v1/resource-sources/{source_id}/catalog")
async def catalog(request: Request, source_id: str) -> dict:
    async with validation():
        return await library(request).catalog(source_id)


@router.post("/v1/resource-sources/{source_id}/refresh")
async def refresh(request: Request, source_id: str) -> dict:
    async with validation():
        return await library(request, True).catalog(source_id, refresh=True)


@router.get("/v1/resource-sources/{source_id}/items/{resource_key}")
async def detail(
    request: Request,
    source_id: str,
    resource_key: str,
    revision: str = Query(pattern=r"^[0-9a-f]{40}$"),
) -> dict:
    async with validation():
        return await library(request).detail(source_id, revision, resource_key)


@router.get("/v1/resource-sources/{source_id}/items/{resource_key}/file")
async def content(
    request: Request,
    source_id: str,
    resource_key: str,
    path: str,
    revision: str = Query(pattern=r"^[0-9a-f]{40}$"),
):
    async with validation():
        service = library(request)
        snapshot, item = service.item(source_id, revision, resource_key)
        if path not in {p["path"] for p in [*item["files"], *item["previews"]]}:
            raise HTTPException(404, "文件不在资源声明范围内")
        data = await service.file(snapshot, join(item["base"], path))
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if not (mime.startswith(("image/", "video/", "audio/")) and mime != "image/svg+xml"):
            mime = "application/octet-stream"
        return Response(
            data,
            media_type=mime,
            headers={"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"},
        )


@router.post("/v1/resource-library/imports")
async def install(request: Request, body: ResourceImportInput) -> dict:
    async with validation():
        return await library(request, True).install(
            body.source_id,
            body.revision,
            body.resource_key,
            body.digest,
            body.names,
            body.configuration,
        )


@router.get("/v1/resource-library/imports")
async def imports(request: Request) -> dict:
    return {"items": await library(request).receipts()}
