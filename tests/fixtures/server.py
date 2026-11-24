import argparse
import asyncio
import base64
import json
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Request, HTTPException
from fake_feishu import FakeFeishu

from dsh_core_worker.service import create_app

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--web-root", type=Path)
parser.add_argument("--mock-models", action="store_true")
parser.add_argument("--mock-model-delay", type=float, default=0.05)
parser.add_argument("--mock-external", action="store_true")
parser.add_argument("--retired-provider-fixture", action="store_true")
args = parser.parse_args()
if args.retired_provider_fixture:
    from dsh_core_worker.retired_providers import RETIRED_PROVIDER_KINDS
    from dsh_core_worker.store import Store

    store = Store(args.root / "state")
    providers = [
        {"id": kind, "kind": kind, "name": "Retired fixture", "models": [{"id": kind + "-model"}]}
        for kind in sorted(RETIRED_PROVIDER_KINDS)
    ]
    store.save_config("catalog", {
        "providers": providers, "suppliers": [],
        "default_model_ids": {"speech_recognition": "senseaudio_asr-model"},
    }, 0)
    store.save_config("connections", [
        {"id": p["id"], "kind": p["kind"], "credential_ref": "PROVIDER_" + p["id"]}
        for p in providers
    ], 0)
    for provider in providers:
        store.save_config("credential:PROVIDER_" + provider["id"], "isolated-fixture-secret", 0)
    store.close()
app = create_app(args.root, args.web_root, origin=f"http://127.0.0.1:{args.port}")
pair = app.state.access.pair("DSH")
(args.root / "fixture-pair.json").write_text(json.dumps(pair))
(args.root / "fixture-pair.json").chmod(0o600)
(args.root / "fixture-launch.txt").write_text(app.state.access.ticket())
(args.root / "fixture-launch.txt").chmod(0o600)
remote = FakeFeishu()
if args.mock_external:

    @app.get("/_fixture/external")
    async def external_metadata():
        return {"url": remote.url, "id": remote.id, "writes": remote.writes}

    app.router.routes.insert(0, app.router.routes.pop())

    @app.post("/_fixture/external")
    async def external_fault(request: Request):
        body = await request.json()
        remote.timeout_after_append = body.get("timeout_after_append", False)
        return {"configured": True}

    app.router.routes.insert(0, app.router.routes.pop())


@app.post("/_fixture/launch")
async def launch(request: Request):
    session = app.state.accounts.session(request.cookies.get("core_session", ""))
    if (
        not session
        or session["account_id"] != app.state.accounts.initial["id"]
        or request.headers.get("x-core-csrf") != session["csrf"]
    ):
        raise HTTPException(403)
    return {"url": f"http://127.0.0.1:{args.port}/#ticket=" + app.state.access.ticket()}


app.router.routes.insert(0, app.router.routes.pop())
if args.mock_models or args.mock_external:
    original = app.router.lifespan_context

    @asynccontextmanager
    async def fixture_lifespan(application):
        async with original(application):

            async def execute(task, request, connection, emit, scratch):
                if request.capability == "speech_recognition":
                    await emit("assistant_delta", {"text": "隔离模型验收结果"})
                    return {
                        "segments": [
                            {
                                "speaker": "speaker-1",
                                "start_ms": 0,
                                "end_ms": 1000,
                                "text": "隔离模型验收结果",
                            }
                        ]
                    }
                if request.capability in ("chat", "vision", "tools"):
                    for text in ["独立 Core ", "测试回复，已保留会话历史。"]:
                        await emit("assistant_delta", {"text": text})
                        await asyncio.sleep(args.mock_model_delay)
                    return {
                        "usage": {
                            "input_tokens": 11,
                            "output_tokens": 9,
                            "total_tokens": 20,
                        }
                    }
                image = base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg=="
                )
                resource = application.state.runtime.resources.put(
                    image, "fixture.png", "image/png", request.application_id
                )
                return {"resources": [resource]}

            if args.mock_models:
                application.state.runtime.models.execute = execute
            if args.mock_external:
                application.state.bridges.external_sync.reads.transport = (
                    remote.transport
                )
                application.state.bridges.external_sync.steps.transport = (
                    remote.transport
                )
            yield

    app.router.lifespan_context = fixture_lifespan
uvicorn.run(
    app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning"
)
