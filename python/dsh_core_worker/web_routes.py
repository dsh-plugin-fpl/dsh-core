"""FPL page protocol adapters using only Core-owned application operations."""

import asyncio
import json

from fastapi import Request
from fastapi.responses import StreamingResponse

from .catalog import CATEGORIES
from .contracts import CoreError


def register_routes(app, content_response):
    def route(path, methods, handler):
        for method in methods:
            app.add_api_route(
                path,
                handler,
                methods=[method],
                name=handler.__name__ + path + method,
                operation_id=method.lower()
                + "_"
                + path.replace("/", "_").replace("{", "").replace("}", ""),
            )

    async def catalog(request: Request):
        return app.state.catalog.view()

    route("/v1/model-providers", ["GET"], catalog)

    async def categories(request: Request):
        return {
            "items": [
                {"id": i, "label": label, "type": kind, "sub_type": None}
                for i, label, kind in CATEGORIES
            ]
        }

    route("/v1/model-categories", ["GET"], categories)

    async def mutate_catalog(request: Request):
        path = request.url.path
        body = await request.json() if request.method != "DELETE" else {}
        from .page_contract import validate_page_input

        validate_page_input(request.scope["route"].path, request.method, body)
        entity = (
            "supplier"
            if "model-suppliers" in path
            else "model"
            if ("model-entries" in path or path.endswith("/models"))
            else "provider"
        )
        params = request.path_params
        identity = params.get("model_id") or params.get("supplier_id") or params.get("provider_id")
        parent = params.get("provider_id") if path.endswith("/models") else None
        if parent:
            identity = None
        action = (
            "create"
            if request.method == "POST" and not path.endswith("/enabled")
            else "delete"
            if request.method == "DELETE"
            else "update"
        )
        return app.state.catalog.mutate(
            entity,
            action,
            body,
            identity,
            parent,
            int(request.query_params.get("expected_catalog_revision", -1)),
        )

    for path, methods in [
        ("/v1/model-providers", ["POST"]),
        ("/v1/model-providers/{provider_id}", ["PATCH", "DELETE"]),
        ("/v1/model-providers/{provider_id}/enabled", ["POST"]),
        ("/v1/model-providers/{provider_id}/models", ["POST"]),
        ("/v1/model-entries/{model_id}", ["PATCH", "DELETE"]),
        ("/v1/model-suppliers", ["POST"]),
        ("/v1/model-suppliers/{supplier_id}", ["PATCH", "DELETE"]),
    ]:
        route(path, methods, mutate_catalog)

    async def defaults(request: Request):
        body = await request.json()
        value = app.state.catalog.saved()
        for model_id in body["default_model_ids"].values():
            app.state.catalog.find(model_id)
        value["default_model_ids"] = body["default_model_ids"]
        value.pop("revision")
        return app.state.catalog.save(value, body["expected_revision"])

    route("/v1/model-catalog/defaults", ["PUT"], defaults)

    async def agent_catalog(request: Request):
        runtime = app.state.runtime
        config = runtime.store.config("agents", {})
        from .contracts import AGENTS

        values = []
        for identity in AGENTS:
            try:
                connection = (
                    runtime.agents.connection(identity)
                    if config["value"].get(identity, {}).get("enabled", True)
                    else None
                )
            except CoreError:
                connection = None
            cached = runtime.store.config("agent-detection:" + identity, {})["value"]
            values.append(
                {
                    "id": identity,
                    "name": {
                        "codex": "Codex",
                        "claude-code": "Claude Code",
                        "opencode": "OpenCode",
                        "dsh": "DeepSeek Harness",
                        "pi": "Pi CLI",
                    }[identity],
                    "executable": connection[1] if connection else "",
                    "enabled": bool(connection),
                    "status": "disabled" if not connection else cached.get("status", "unchecked"),
                    "available": bool(connection),
                    "version": str(cached.get("version", "")),
                    "authenticated": cached.get("authenticated", False),
                    "models": cached.get("models", []),
                    "message": cached.get("message", "点击重新检测读取 CLI 登录与模型状态"),
                    "configured": bool(connection),
                    "purposes": [],
                    "supported_purposes": [],
                    "connection_status": "ready" if connection else "disabled",
                    "recommended": identity == "codex",
                    "model_source": "CLI",
                    "diagnostic": None,
                }
            )
        return {"items": values, "can_enable": True, "revision": config["revision"]}

    async def agents(request: Request):
        if request.method == "POST":
            runtime = app.state.runtime
            if request.path_params.get("agent_id"):
                state = runtime.store.config("agents", {})
                state["value"][request.path_params["agent_id"]] = {
                    "id": request.path_params["agent_id"],
                    "enabled": True,
                }
                await runtime.invoke("config.save", {"key": "agents", **state})
            else:
                from .contracts import AGENTS

                for identity in AGENTS:
                    try:
                        value = await runtime.agents.detect(identity)
                    except (CoreError, OSError, TimeoutError):
                        value = {"available": False, "message": "CLI 未安装、未登录或检测失败"}
                    current = runtime.store.config("agent-detection:" + identity)
                    runtime.store.save_config(
                        "agent-detection:" + identity, value, current["revision"]
                    )
        return await agent_catalog(request)

    route("/v1/code-agents", ["GET"], agents)
    route("/v1/code-agents/detect", ["POST"], agents)
    route("/v1/code-agents/{agent_id}/enable", ["POST"], agents)

    async def local_rows():
        runtime = app.state.runtime
        state = await runtime.local.status()
        tasks = runtime.store.tasks()
        rows = []
        for model in state["items"]:
            job = next(
                (
                    t
                    for t in tasks
                    if t["request"].get("kind") == "local"
                    and t["connection"].get("kind") == "local_management"
                    and t["request"]["model"] == model["id"]
                ),
                None,
            )
            active = job and job["status"] in ("queued", "running", "processing")
            rows.append(
                {
                    **model,
                    "revision": state["revision"],
                    "status": "installing"
                    if active
                    else "installed"
                    if model["installed"]
                    else "failed"
                    if job and job["status"] == "failed"
                    else "not_installed",
                    "progress": job["progress"] if job else 0,
                    "error_message": (job.get("error") or {}).get("message") if job else None,
                }
            )
        return {"items": rows, "can_manage": True}

    async def local_models(request: Request):
        if request.method == "GET":
            return await local_rows()
        body = await request.json()
        identity = request.path_params.get("model_id", body.get("model_id"))
        if request.url.path.endswith("/attach"):
            from .extracted.config.local_models import definition

            model = definition(identity)
            kind = (
                "local_ffmpeg"
                if "ffmpeg" in identity
                else "local_relayvsr"
                if "relay" in identity.lower()
                else "local_zonos_tts"
                if "zonos" in identity.lower()
                else "local_qwen_tts"
            )
            view = app.state.catalog.saved()
            provider_id = "local-" + identity
            if not any(p["id"] == provider_id for p in view["providers"]):
                view["providers"].append(
                    {
                        "id": provider_id,
                        "name": model["label"],
                        "kind": kind,
                        "base_url": "",
                        "enabled": True,
                        "supplier_id": None,
                        "origin": "user",
                        "managed_fields": [],
                        "models": [
                            app.state.catalog.model(
                                provider_id,
                                {
                                    "slug": identity,
                                    "label": model["label"],
                                    "capabilities": [model["capability"]],
                                },
                            )
                        ],
                    }
                )
            view.pop("revision")
            return app.state.catalog.save(view, body["expected_catalog_revision"])
        current = app.state.runtime.store.config("local", {"enabled": []})
        if body["expected_revision"] != current["revision"]:
            raise CoreError("本地模型状态已更新", "revision_conflict")
        active = next(
            (
                t
                for t in app.state.runtime.store.tasks()
                if t["request"]["model"] == identity
                and t["connection"].get("kind") == "local_management"
                and t["status"] in ("queued", "running")
            ),
            None,
        )
        if (
            active
            and body["action"] != "cancel"
            and active["request"]["request_id"] != body["request_id"]
        ):
            raise CoreError("该本地模型正在执行管理任务，请等待完成或取消", "task_busy")
        if body["action"] == "cancel":
            task = next(
                (
                    t
                    for t in app.state.runtime.store.tasks()
                    if t["request"]["model"] == identity
                    and t["connection"].get("kind") == "local_management"
                    and t["status"] in ("queued", "running")
                ),
                None,
            )
            if task:
                await app.state.runtime.invoke("task.cancel", {"id": task["id"]})
        else:
            await app.state.runtime.invoke(
                "local.manage",
                {"request_id": body["request_id"], "model_id": identity, "action": body["action"]},
            )
        return next(m for m in (await local_rows())["items"] if m["id"] == identity)

    route("/v1/local-models", ["GET"], local_models)
    route("/v1/local-models/attach", ["POST"], local_models)
    route("/v1/platform/local-models/{model_id}/actions", ["POST"], local_models)

    async def conversations(request: Request):
        c = app.state.conversations
        identity = request.path_params.get("conversation_id")
        if request.method == "GET":
            return c.get(identity) if identity else c.list()
        body = await request.json() if request.method != "DELETE" else {}
        if not identity:
            return c.create(body.get("model_id"))
        if request.method == "DELETE":
            revision = int(request.query_params["expected_revision"])
            value = c.get(identity)
            if any(m["status"] == "pending" for m in value["messages"]):
                raise CoreError("请先停止当前回复", "task_busy")
            c.runtime.store.save_config("conversation:" + identity, None, revision)
            return {"deleted": True}
        allowed = {"title", "model_id", "system_prompt", "temperature", "max_tokens"}
        return c.save(
            identity, {k: v for k, v in body.items() if k in allowed}, body["expected_revision"]
        )

    route("/v1/model-test/conversations", ["GET", "POST"], conversations)
    route(
        "/v1/model-test/conversations/{conversation_id}", ["GET", "PATCH", "DELETE"], conversations
    )

    async def conversation_stream(request: Request):
        identity = request.path_params["conversation_id"]
        task, value, message = await app.state.conversations.send(
            identity, await request.json(), "/regenerate/" in request.url.path
        )

        async def generate():
            seq, cursor = 0, 0

            def event(kind, payload):
                nonlocal seq
                seq += 1
                return (
                    "data: "
                    + json.dumps(
                        {"type": kind, "schema_version": 2, "sequence": seq, "payload": payload}
                    )
                    + "\n\n"
                )

            yield event("start", {"assistant_message_id": message["id"], "conversation": value})
            while not await request.is_disconnected():
                rows = app.state.runtime.store.events(task["id"], cursor)
                for row in rows:
                    cursor = row["seq"]
                    if row["kind"] == "assistant_delta":
                        yield event("delta", row["value"])
                current = app.state.runtime.store.task(task["id"])
                if (
                    current["status"] in ("completed", "failed", "interrupted", "cancelled")
                    and len(rows) < 500
                ):
                    if current["error"]:
                        yield event("error", {"message": current["error"]["message"]})
                    yield event("done", {"conversation": app.state.conversations.get(identity)})
                    return
                await asyncio.sleep(0.1)

        return StreamingResponse(generate(), media_type="text/event-stream")

    route(
        "/v1/model-test/conversations/{conversation_id}/messages/stream",
        ["POST"],
        conversation_stream,
    )
    route(
        "/v1/model-test/conversations/{conversation_id}/regenerate/stream",
        ["POST"],
        conversation_stream,
    )

    async def conversation_stop(request: Request):
        value = app.state.conversations.get(request.path_params["conversation_id"])
        for message in value["messages"]:
            if message["status"] == "pending":
                await app.state.runtime.invoke("task.cancel", {"id": message["task_id"]})
        return app.state.conversations.get(value["id"])

    route("/v1/model-test/conversations/{conversation_id}/stop", ["POST"], conversation_stop)

    async def attachments(request: Request):
        c, identity = app.state.conversations, request.path_params["conversation_id"]
        value = c.get(identity)
        if request.method == "GET":
            return content_response(request.path_params["attachment_id"])
        draft = value["draft_attachments"]
        if request.method == "DELETE":
            draft = [a for a in draft if a["id"] != request.path_params["attachment_id"]]
        else:
            form = await request.form()
            for upload in form.getlist("files"):
                try:
                    data = await upload.read(20 * 1024 * 1024 + 1)
                    if len(data) > 20 * 1024 * 1024 or not upload.content_type.startswith("image/"):
                        raise CoreError("请上传 20 MB 以内的图片")
                    resource = c.runtime.resources.put(data, upload.filename, upload.content_type)
                    draft.append(c.attachment(resource))
                finally:
                    await upload.close()
        c.save(identity, {"draft_attachments": draft}, value["revision"])
        return {"items": draft}

    route("/v1/model-test/conversations/{conversation_id}/attachments", ["POST"], attachments)
    route(
        "/v1/model-test/conversations/{conversation_id}/attachments/{attachment_id}",
        ["DELETE"],
        attachments,
    )
    route(
        "/v1/model-test/conversations/{conversation_id}/attachments/{attachment_id}/content",
        ["GET"],
        attachments,
    )

    from .task_views import TaskViews

    views = TaskViews(app, content_response)
    for path, methods in views.routes():
        route(path, methods, views.handle)
