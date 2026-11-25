"""Fixed website routes; local identity and remote authentication never share cookies."""

import base64
import json
import re
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..integrations.contracts.errors import (
    DuplicateIntegrationError,
    IntegrationNotFoundError,
    IntegrationValidationError,
)
from ..integrations.snapshots import files
from .installations import KINDS, public_manifest

router = APIRouter(tags=["fpl-creative-workshop"])


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: str = Field(default="http://127.0.0.1:3000", max_length=2048)
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256, repr=False)


class ChangePassword(BaseModel):
    current_password: str = Field(min_length=1, max_length=256, repr=False)
    new_password: str = Field(min_length=1, max_length=256, repr=False)


class Preview(BaseModel):
    release_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    names: dict[str, str] = Field(default_factory=dict, max_length=30)


class Apply(Preview):
    preview_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")


def website(request):
    return request.app.state.fpl_website


def installer(request):
    return request.app.state.creative_installations


def identity(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise HTTPException(422, "资源标识无效")
    return value


@router.get("/v1/fpl-connection")
async def status(request: Request):
    return await website(request).status()


@router.post("/v1/fpl-connection/login")
async def login(request: Request, body: Login):
    return await website(request).login(body.site, body.username, body.password)


@router.post("/v1/fpl-connection/logout")
async def logout(request: Request):
    return await website(request).logout()


@router.post("/v1/fpl-connection/change-password")
async def change_password(request: Request, body: ChangePassword):
    return await website(request).change_password(body.model_dump())


async def forward(request, method, path):
    remote = website(request)
    async with remote.lock:
        state = remote.load()
        kwargs = {}
        if method == "GET":
            kwargs["params"] = dict(request.query_params)
        else:
            raw = await request.body()
            if len(raw) > 28 * 1024 * 1024:
                raise HTTPException(413, "上传内容超过限制")
            kwargs.update(
                content=raw,
                headers={
                    "Content-Type": request.headers.get("content-type", "application/json"),
                    "Origin": state["site"],
                    "X-CSRF-Token": next(
                        (c["value"] for c in state.get("cookies", []) if c["name"] == "fpl_csrf"),
                        "",
                    ),
                },
            )
        operation = str(uuid4())
        store = request.app.state.runtime.store
        key = "creative-operation:" + operation
        if method != "GET":
            store.save_config(
                key,
                {
                    "id": operation,
                    "site": state["site"],
                    "method": method,
                    "path": path,
                    "status": "unknown",
                },
                0,
            )
        try:
            raw, content_type = await remote.send(state, method, path, **kwargs)
        except HTTPException as error:
            if method != "GET" and error.status_code == 502:
                raise HTTPException(
                    502,
                    "网站响应中断，操作结果待核对；请在“我的创作”刷新状态，不要重复提交。回执："
                    + operation,
                ) from None
            raise
        if method != "GET":
            row = store.config(key, {})
            store.save_config(key, {**row["value"], "status": "completed"}, row["revision"])
        return Response(raw, media_type=content_type, headers={"Cache-Control": "no-store"})


@router.get("/v1/creative-workshop/operations/{operation_id}")
async def operation(request: Request, operation_id: str):
    row = request.app.state.runtime.store.config(
        "creative-operation:" + identity(operation_id), None
    )["value"]
    if not row:
        raise HTTPException(404, "操作回执不存在")
    return row


@router.get("/v1/creative-workshop/items")
async def items(request: Request):
    return await forward(request, "GET", "/v1/creative-workshop/items")


@router.post("/v1/creative-workshop/items")
async def create(request: Request):
    return await forward(request, "POST", "/v1/creative-workshop/items")


@router.post("/v1/creative-workshop/items/from-integration")
async def from_integration(request: Request, body: dict):
    async with website(request).lock:
        return await publish_local_draft(request, body)


async def publish_local_draft(request, body):
    kind = body.get("kind")
    if kind not in KINDS:
        raise HTTPException(422, "能力类型无效")
    try:
        local = await installer(request).portable.read(KINDS[kind], body.get("source_id", ""))
    except IntegrationNotFoundError:
        raise HTTPException(404, "本机集成不存在") from None
    config = local.config
    manifest = public_manifest(kind, config)
    if kind == "mcp" and config.get("transport") == "stdio":
        # The draft only contains a hint; no command, environment or local paths are uploaded.
        manifest = {"transport": "stdio", "command_hint": "请在本机安装并配置此 MCP"}

    # A local configuration may contain secrets in URL/query fields or attachments.
    def public_only(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if any(
                    word in key.lower()
                    for word in (
                        "secret",
                        "password",
                        "token",
                        "authorization",
                        "api_key",
                        "apikey",
                    )
                ):
                    raise HTTPException(422, "公开配置含凭据字段，请先移除")
                public_only(child)
        elif isinstance(value, list):
            for child in value:
                public_only(child)
        elif isinstance(value, str) and ("Bearer " in value or "-----BEGIN " in value):
            raise HTTPException(422, "公开配置疑似含凭据，请先移除")

    public_only(manifest)
    if manifest.get("url"):
        url = urlsplit(manifest["url"])
        if url.username or url.password or url.query or url.fragment:
            raise HTTPException(422, "公开地址不能包含账号、查询参数或片段")
    if kind == "skill":
        private_text = re.compile(
            rb"(?i)(-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|authorization\s*[:=]\s*bearer\s+\S+|(?:api[_-]?key|client[_-]?secret|password)\s*[:=]\s*['\"]?[^\s'\"]{8,})"
        )
        for name, content in files(local.archive_base64).items():
            if (
                name.rsplit("/", 1)[-1].lower()
                in {".env", ".npmrc", "id_rsa", "id_ed25519", "credentials.json", "secrets.json"}
                or name.lower().endswith((".key", ".pem"))
                or private_text.search(content)
            ):
                raise HTTPException(422, "Skill 包含私有文件或凭据，请先移除")
    draft = {
        "kind": kind,
        "title": local.name,
        "summary": config.get("description", ""),
        "manifest": manifest,
        "tags": config.get("tags", []),
        "components": [],
    }
    remote = website(request)
    state = remote.load()
    raw, _ = await remote.send(state, "POST", "/v1/creative-workshop/items", json=draft)
    item = json.loads(raw)
    if kind == "skill":
        raw, _ = await remote.send(
            state,
            "POST",
            "/v1/creative-workshop/items/" + identity(item["id"]) + "/skill-archive",
            data={"expected_revision": str(item["revision"])},
            files={
                "file": ("skill.zip", base64.b64decode(local.archive_base64), "application/zip")
            },
        )
        item = json.loads(raw)
    return item


@router.get("/v1/creative-workshop/items/{item_id}")
async def item(request: Request, item_id: str):
    return await forward(request, "GET", "/v1/creative-workshop/items/" + identity(item_id))


@router.patch("/v1/creative-workshop/items/{item_id}")
async def update(request: Request, item_id: str):
    return await forward(request, "PATCH", "/v1/creative-workshop/items/" + identity(item_id))


@router.post("/v1/creative-workshop/items/{item_id}/{action}")
async def action(request: Request, item_id: str, action: str):
    if action not in {"publish", "withdraw", "cover", "skill-archive"}:
        raise HTTPException(404, "操作不存在")
    return await forward(
        request, "POST", "/v1/creative-workshop/items/" + identity(item_id) + "/" + action
    )


@router.get("/v1/creative-workshop/items/{item_id}/cover")
async def cover(request: Request, item_id: str):
    return await forward(
        request, "GET", "/v1/creative-workshop/items/" + identity(item_id) + "/cover"
    )


@router.get("/v1/creative-workshop/releases/{release_id}")
async def release(request: Request, release_id: str):
    return await forward(request, "GET", "/v1/creative-workshop/releases/" + identity(release_id))


@router.get("/v1/creative-workshop/releases/{release_id}/cover")
async def release_cover(request: Request, release_id: str):
    return await forward(
        request, "GET", "/v1/creative-workshop/releases/" + identity(release_id) + "/cover"
    )


@router.post("/v1/creative-workshop/installations/preview")
async def preview(request: Request, body: Preview):
    remote = website(request)
    async with remote.lock:
        return await installer(request).preview(remote.load(), body.model_dump())


@router.post("/v1/creative-workshop/installations")
async def install(request: Request, body: Apply):
    remote = website(request)
    async with remote.lock:
        try:
            plan = installer(request).config(body.preview_id)
            if not plan or plan["release_id"] != body.release_id:
                raise HTTPException(409, "安装版本与预览不一致，请重新检查")
            return await installer(request).apply(remote.load(), body.preview_id)
        except DuplicateIntegrationError:
            raise HTTPException(409, "目标名称已被占用，请重新预览") from None
        except (ValueError, IntegrationValidationError):
            raise HTTPException(422, "安装配置无效，请检查名称和能力内容") from None


@router.get("/v1/creative-workshop/installations/{installation_id}")
async def receipt(request: Request, installation_id: str):
    return await installer(request).receipt(identity(installation_id))


@router.post("/v1/creative-workshop/installations/{installation_id}/stdio")
async def stdio(request: Request, installation_id: str, body: dict):
    remote = website(request)
    async with remote.lock:
        try:
            return await installer(request).configure_stdio(identity(installation_id), body)
        except DuplicateIntegrationError:
            raise HTTPException(409, "本机名称已被占用，请先在集成中心处理冲突") from None
        except (ValueError, KeyError):
            raise HTTPException(422, "请填写有效的本机命令与参数") from None
