"""Standalone loopback HTTP application. Transport adapters own no database queries."""

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from .access import Access
from .applications import ApplicationListOut, Applications
from .bridges import Bridge, Bridges
from .catalog import Catalog
from .contracts import CoreError, Execution
from .conversations import Conversations
from .credentials import ProviderCredentials
from .http_schemas import BridgeListOut, ResourceListOut, TaskListOut, TaskOut
from .runtime import Runtime


class LaunchIn(BaseModel):
    ticket: str = Field(max_length=200)


class PairIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class RpcIn(BaseModel):
    method: str = Field(max_length=80)
    params: dict = Field(default_factory=dict)


def create_tenant_app(root, web_root=None, origin="http://127.0.0.1:9310", *, account_access=None):
    root = Path(root).resolve()
    web_root = Path(web_root).resolve() if web_root else None
    access = account_access or Access(root / "access")
    bridges = Bridges(access)

    @asynccontextmanager
    async def lifespan(app):
        async def notify(method, params):
            pass  # Durable events are read from Runtime, never from a lossy UI subscriber.

        runtime = Runtime(root / "state", bridges.host, notify)
        bridges.runtime = runtime
        bridges.catalog = Applications(runtime.store, access, bridges.online)
        credentials = ProviderCredentials(runtime.store, access.cipher)
        bridges.credentials = credentials
        app.state.credentials = credentials
        app.state.runtime = runtime
        from .storage_history import StorageHistory

        app.state.imported_history = StorageHistory(runtime.store.root, initialize=True)
        from .file_library import FileLibrary

        FileLibrary(runtime.resources, initialize=True)
        app.state.catalog = Catalog(runtime, credentials, bridges)
        bridges.model_catalog_view = app.state.catalog
        app.state.conversations = Conversations(runtime, app.state.catalog)
        from .integrations.assembly import Integrations

        app.state.integrations = Integrations(root / "integrations", runtime, access)
        try:
            await app.state.integrations.database.migrate()
            from .integrations.external_schema import migrate as migrate_external

            await migrate_external(app.state.integrations.database)
            from .integrations.external_core import ExternalSync

            bridges.external_sync = ExternalSync(runtime, app.state.integrations, access)
            await bridges.external_sync.steps.recover()
            from .integrations.transfers import Transfers

            app.state.integration_transfers = Transfers(app.state.integrations, bridges, access)
            app.state.integration_validation_recovery = app.state.integrations.jobs
            from .creative.connection import Website
            from .creative.installations import Installations

            app.state.fpl_website = Website(access)
            app.state.creative_installations = Installations(app.state.fpl_website, app.state.integrations)
            yield
        finally:
            for bridge in list(bridges.online.values()):
                bridge.disconnect()
                await bridge.socket.close(code=1001)
            await app.state.integrations.close()
            await runtime.close()

    app = FastAPI(
        title="FPL Core",
        version="0.4.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/v1/openapi.json",
    )
    app.state.access, app.state.bridges = access, bridges
    expected_host = origin.split("://", 1)[1]

    @app.middleware("http")
    async def owner_access(request, call_next):
        if request.headers.get("host") != expected_host:
            return JSONResponse({"code": "invalid_host", "detail": "服务来源无效"}, 403)
        path = request.url.path
        content_length = request.headers.get("content-length")
        maximum = (
            33 * 1024 * 1024
            if request.headers.get("content-type", "").startswith("application/json")
            else 257 * 1024 * 1024
        )
        if content_length and (not content_length.isdigit() or int(content_length) > maximum):
            return JSONResponse(
                {"code": "request_too_large", "detail": "请求内容超过大小限制"}, 413
            )
        if request.method not in ("GET", "HEAD") and request.headers.get("origin") != origin:
            return JSONResponse({"code": "invalid_origin", "detail": "请求来源无效"}, 403)
        if path.startswith("/v1/") and path != "/v1/session":
            session = access.session(request.cookies.get("core_session"))
            if not session:
                return JSONResponse(
                    {"code": "unauthorized", "detail": "请通过启动入口打开看板"}, 401
                )
            if request.method not in ("GET", "HEAD") and not secrets.compare_digest(
                session["csrf"], request.headers.get("x-core-csrf", "")
            ):
                return JSONResponse({"code": "csrf", "detail": "请求验证失效，请刷新"}, 403)
        response = await call_next(request)
        if path.startswith("/v1/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(CoreError)
    async def core_error(request, error):
        status = {
            "not_found": 404,
            "unauthorized": 401,
            "forbidden": 403,
            "revision_conflict": 409,
            "idempotency_conflict": 409,
            "upload_conflict": 409,
            "task_busy": 409,
            "profile_busy": 409,
        }.get(error.code, 422)
        return JSONResponse({"code": error.code, "detail": str(error), **error.details}, status)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        return JSONResponse({"code": "validation", "detail": "请求字段或参数不符合接口要求"}, 422)

    async def invalid_value(request, error):
        return JSONResponse({"code": "validation", "detail": "请求字段或参数不符合接口要求"}, 422)

    for error_type in (ValueError, KeyError, TypeError):
        app.add_exception_handler(error_type, invalid_value)

    @app.get("/health")
    @app.get("/ready")
    async def health():
        return {
            "status": "ready",
            "service": "fpl-core",
            "version": "0.4.0",
            "instance": (root / "instance").read_text() if (root / "instance").exists() else "",
        }

    @app.post("/v1/session")
    async def session(body: LaunchIn):
        value, csrf = access.exchange(body.ticket)
        response = JSONResponse(
            {
                "csrf": csrf,
                "owner": access.profile()
                if hasattr(access, "profile")
                else {"id": "local-owner", "name": "本机所有者"},
            }
        )
        response.set_cookie("core_session", value, httponly=True, samesite="strict", max_age=86400)
        response.set_cookie("core_csrf", csrf, samesite="strict", max_age=86400)
        return response

    @app.get("/v1/session")
    async def current_session():
        return {
            "owner": access.profile()
            if hasattr(access, "profile")
            else {"id": "local-owner", "name": "本机所有者"}
        }

    @app.get("/v1/state")
    async def state():
        value = await app.state.runtime.invoke("state", {})
        return {
            **value,
            "harness": bridges.model_catalog(),
            "bridges": [{**v, "connected": v["id"] in bridges.online} for v in access.bridges()],
        }

    @app.get("/v1/applications", response_model=ApplicationListOut)
    async def application_catalog():
        return {"items": bridges.catalog.items()}

    @app.post("/v1/rpc")
    async def invoke(body: RpcIn):
        allowed = {
            "hello",
            "state",
            "agent.detect",
            "config.save",
            "resource.put",
            "resource.read",
            "resource.chunk",
            "resource.upload.begin",
            "resource.upload.append",
            "resource.upload.status",
            "resource.upload.finish",
            "resource.upload.abort",
            "task.submit",
            "task.get",
            "task.events",
            "task.cancel",
            "task.resume",
            "local.manage",
            "voice.command",
            "voice.events",
        }
        if body.method not in allowed:
            raise CoreError("操作未开放", "forbidden")
        params = body.params
        if body.method in ("task.submit", "resource.put", "resource.upload.begin"):
            params = {**params, "application_id": "core"}
        return await app.state.runtime.invoke(body.method, params)

    from .file_library_api import install as install_file_library

    install_file_library(app)
    from .storage_history import register as register_imported_history

    register_imported_history(app)

    @app.post("/v1/tasks", response_model=TaskOut)
    async def submit(body: Execution):
        return await app.state.runtime.invoke(
            "task.submit", {**body.model_dump(), "application_id": "core", "tools": []}
        )

    @app.get("/v1/tasks", response_model=TaskListOut)
    async def tasks():
        return {"items": app.state.runtime.store.tasks()}

    @app.get("/v1/tasks/{identity}", response_model=TaskOut)
    async def task(identity: str):
        return app.state.runtime.store.task(identity)

    @app.post("/v1/tasks/{identity}/cancel", response_model=TaskOut)
    async def cancel(identity: str):
        return await app.state.runtime.invoke("task.cancel", {"id": identity})

    @app.get("/v1/tasks/{identity}/events")
    async def events(identity: str, after: int = 0):
        app.state.runtime.store.task(identity)
        return {"items": app.state.runtime.store.events(identity, after)}

    @app.get("/v1/tasks/{identity}/stream")
    async def stream(identity: str, request: Request, after: int = 0):
        app.state.runtime.store.task(identity)

        async def generate():
            cursor = after
            while not await request.is_disconnected():
                rows = app.state.runtime.store.events(identity, cursor)
                for row in rows:
                    cursor = row["seq"]
                    yield f"id: {cursor}\nevent: {row['kind']}\ndata: {json.dumps(row)}\n\n"
                value = app.state.runtime.store.task(identity)
                if (
                    value["status"] in ("completed", "failed", "interrupted", "cancelled")
                    and len(rows) < 500
                ):
                    yield "event: settled\ndata: " + json.dumps(value) + "\n\n"
                    return
                await asyncio.sleep(0.25)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/v1/resources", response_model=ResourceListOut)
    async def resources():
        return {"items": app.state.runtime.resources.list()}

    @app.post("/v1/resources")
    async def upload(request: Request):
        form = await request.form(max_files=30)
        result = []
        for value in form.values():
            if hasattr(value, "read"):
                data = await value.read(256 * 1024 * 1024 + 1)
                try:
                    result.append(
                        app.state.runtime.resources.put(
                            data,
                            value.filename or "file",
                            value.content_type or "application/octet-stream",
                        )
                    )
                finally:
                    await value.close()
        return {"items": result}

    def content_response(identity, download=False):
        path, info = app.state.runtime.resources.path(identity)
        safe = (
            info["mime_type"].startswith(("image/", "audio/", "video/"))
            and info["mime_type"] != "image/svg+xml"
        )
        return FileResponse(
            path,
            media_type=info["mime_type"] if safe else "application/octet-stream",
            filename=info["name"],
            content_disposition_type="attachment" if download or not safe else "inline",
            headers={"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/v1/resources/{identity}/content")
    async def resource_content(identity: str, download: bool = False):
        return content_response(identity, download)

    @app.get("/v1/bridges", response_model=BridgeListOut)
    async def bridge_list():
        return {"items": [{**v, "connected": v["id"] in bridges.online} for v in access.bridges()]}

    @app.post("/v1/bridges")
    async def pair(body: PairIn):
        return access.pair(body.name)

    @app.delete("/v1/bridges/{identity}")
    async def revoke(identity: str):
        access.revoke(identity)
        if bridge := bridges.online.get(identity):
            bridge.disconnect()
            await bridge.socket.close(code=1008)
        return {"revoked": True}

    @app.websocket("/v1/bridge")
    async def bridge_socket(socket: WebSocket):
        if socket.headers.get("host") != expected_host or socket.headers.get("origin") not in (
            None,
            origin,
        ):
            await socket.close(code=1008)
            return
        try:
            identity = access.authenticate_bridge(
                socket.headers.get("authorization", "").removeprefix("Bearer ")
            )
        except CoreError:
            await socket.close(code=1008)
            return
        if identity in bridges.online:
            await socket.close(code=1008)
            return
        await socket.accept()
        bridge = Bridge(identity, socket)
        bridges.online[identity] = bridge

        async def handle(message):
            try:
                result = await bridges.invoke(bridge, message["method"], message.get("params", {}))
                await bridge.send({"id": message["id"], "result": result})
            except Exception as error:  # noqa: BLE001 — sanitize transport/provider boundary
                await bridge.send(
                    {
                        "id": message["id"],
                        "error": {
                            "code": getattr(error, "code", "bridge_error"),
                            "data": error.details if isinstance(error, CoreError) else {},
                            "message": str(error)
                            if isinstance(error, CoreError)
                            else "连接请求失败",
                        },
                    }
                )
            finally:
                bridge.requests.pop(message["id"], None)

        try:
            while True:
                message = await socket.receive_json()
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    await socket.close(code=1008)
                    break
                if "method" in message:
                    if message["method"] == "$/cancelRequest":
                        if job := bridge.requests.get(message.get("params", {}).get("id")):
                            job.cancel()
                    elif (
                        message.get("id")
                        and len(bridge.requests) < 128
                        and message["id"] not in bridge.requests
                    ):
                        bridge.requests[message["id"]] = asyncio.create_task(handle(message))
                    else:
                        await socket.close(code=1008)
                        break
                elif (future := bridge.pending.get(message.get("id"))) and not future.done():
                    if "error" in message:
                        future.set_exception(CoreError("DSH 宿主调用失败", "bridge_call_failed"))
                    else:
                        future.set_result(message.get("result"))
        except (WebSocketDisconnect, ValueError):
            pass
        finally:
            if bridges.online.get(identity) is bridge:
                bridges.online.pop(identity, None)
            pending = list(bridge.requests.values())
            bridge.disconnect()
            await asyncio.gather(*pending, return_exceptions=True)

    from .integrations.assembly import register as register_integrations

    register_integrations(app)

    from .voice_http import register_voice

    register_voice(app, origin)

    from .web_routes import register_routes

    from .creative.http import router as creative_router

    app.include_router(creative_router)
    register_routes(app, content_response)

    @app.get("/settings", include_in_schema=False)
    async def legacy_connections():
        return RedirectResponse("/integrations?kind=external-connections", status_code=307)

    @app.get("/{path:path}", include_in_schema=False)
    async def static(path: str):
        if path.startswith(("v1/", "health/", "ready/")):
            return JSONResponse({"detail": "接口不存在"}, 404)
        if web_root and web_root.is_dir():
            target = (web_root / path).resolve()
            if target.is_relative_to(web_root) and target.is_file():
                return FileResponse(target)
            if "." not in Path(path).name and (web_root / "index.html").is_file():
                return FileResponse(web_root / "index.html", headers={"Cache-Control": "no-store"})
        return JSONResponse({"detail": "请先构建看板"}, 503)

    from .page_contract import install_contract

    install_contract(app)
    return app


def create_app(root, web_root=None, origin="http://127.0.0.1:9310"):
    from .identity_gateway import create_unified_app

    return create_unified_app(root, create_tenant_app, web_root, origin)
