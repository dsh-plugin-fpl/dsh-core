import asyncio
import base64
import shutil

import pytest
from dsh_core_worker.contracts import CoreError, Execution
from dsh_core_worker.local_models import process
from dsh_core_worker.runtime import Runtime
from dsh_core_worker.store import Store


@pytest.fixture
async def runtime(tmp_path):
    async def host(method, params):
        if method == "application.ready":
            return True
        if method == "harness.generate":
            await core.invoke(
                "host.event",
                {
                    "id": params["task_id"],
                    "kind": "assistant_delta",
                    "value": {"text": "Harness says hello"},
                },
            )
            return {"usage": {"total_tokens": 8}}
        raise AssertionError(method)

    async def notify(*args):
        pass

    core = Runtime(tmp_path / "profile", host, notify)
    yield core
    await core.close()


async def settle(runtime, id):
    for _ in range(300):
        task = runtime.store.task(id)
        if task["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return task
        await asyncio.sleep(0.01)
    raise AssertionError("task did not finish")


async def test_harness_task_receipt_stream_idempotency_and_scope(runtime):
    request = {
        "request_id": "same",
        "application_id": "notes",
        "session_id": "one",
        "connection_id": "harness:deepseek",
        "model": "test",
        "prompt": "hello",
    }
    first = await runtime.invoke("task.submit", request)
    second = await runtime.invoke("task.submit", request)
    assert first["id"] == second["id"]
    task = await settle(runtime, first["id"])
    assert task["status"] == "completed", task
    assert task["text"] == "Harness says hello"
    assert any(event["kind"] == "assistant_delta" for event in runtime.store.events(first["id"]))
    assert runtime.store.events(first["id"])[0]["kind"] == "task_status"
    other = await runtime.invoke("task.submit", {**request, "session_id": "two"})
    assert other["id"] != first["id"]
    with pytest.raises(CoreError, match="请求编号"):
        await runtime.invoke("task.submit", {**request, "prompt": "different"})


async def test_config_revision_and_validation(runtime):
    connection = {
        "id": "gateway",
        "name": "Gateway",
        "kind": "openai",
        "models": [{"id": "gpt-test", "capabilities": ["chat"]}],
    }
    saved = await runtime.invoke(
        "config.save", {"key": "connections", "revision": 0, "value": [connection]}
    )
    assert saved["revision"] == 1
    with pytest.raises(CoreError) as error:
        await runtime.invoke("config.save", {"key": "connections", "revision": 0, "value": []})
    assert error.value.code == "revision_conflict"
    with pytest.raises(CoreError) as error:
        await runtime.invoke(
            "config.save",
            {
                "key": "connections",
                "revision": 1,
                "value": [{**connection, "api_key": "never-store"}],
            },
        )
    assert error.value.code == "validation"
    assert b"never-store" not in (runtime.store.root / "core.sqlite3").read_bytes()
    with pytest.raises(CoreError):
        await runtime.invoke(
            "config.save",
            {
                "key": "connections",
                "revision": 1,
                "value": [{**connection, "base_url": "http://public.example"}],
            },
        )


async def test_resources_integrity_and_application_preflight(runtime):
    resource = await runtime.invoke(
        "resource.put",
        {
            "data_base64": base64.b64encode(b"sound").decode(),
            "mime_type": "audio/wav",
            "name": "../ref.wav",
            "application_id": "app-one",
        },
    )
    assert resource["name"] == "ref.wav"
    assert (
        runtime.resources.read(resource["id"])["data_base64"] == base64.b64encode(b"sound").decode()
    )
    request = Execution(request_id="one", application_id="app-two", resource_ids=[resource["id"]])
    with pytest.raises(CoreError) as error:
        runtime.models.reference(request, resource["id"])
    assert error.value.code == "resource_scope"
    path, _ = runtime.resources.path(resource["id"])
    path.write_bytes(b"tampered")
    with pytest.raises(CoreError) as error:
        runtime.resources.path(resource["id"])
    assert error.value.code == "resource_integrity"


async def test_cancel_stops_callback(runtime):
    stopped = asyncio.Event()

    async def blocked(*args):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    runtime.models.execute = blocked
    task = await runtime.invoke(
        "task.submit",
        {
            "request_id": "cancel",
            "application_id": "core",
            "connection_id": "harness:local",
            "model": "test",
        },
    )
    await asyncio.sleep(0.02)
    result = await runtime.invoke("task.cancel", {"id": task["id"]})
    assert result["status"] == "cancelled" and stopped.is_set()


async def test_restart_recovers_only_upstream_receipts(tmp_path):
    store = Store(tmp_path)
    known, _ = store.admit(Execution(request_id="known", application_id="a"), {"kind": "test"})
    unknown, _ = store.admit(Execution(request_id="unknown", application_id="a"), {"kind": "test"})
    store.update(known["id"], status="running", provider_task_id="upstream-123")
    store.update(unknown["id"], status="running")
    store.close()
    store = Store(tmp_path)
    store.recover()
    assert store.task(known["id"])["status"] == "recoverable"
    assert store.task(unknown["id"])["status"] == "interrupted"
    store.close()


async def test_profile_lock(runtime):
    with pytest.raises(CoreError) as error:
        Runtime(runtime.store.root, None, None)
    assert error.value.code == "profile_busy"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed")
async def test_real_ffmpeg_public_task(runtime, tmp_path):
    source = tmp_path / "input.mp4"
    await process(
        "ffmpeg",
        "-nostdin",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=64x64:d=0.15",
        "-c:v",
        "libx264",
        source,
    )
    resource = runtime.resources.put(source.read_bytes(), "input.mp4", "video/mp4")
    task = await runtime.invoke(
        "local.manage",
        {"model_id": "ffmpeg-video-sharpen", "action": "enable", "request_id": "enable"},
    )
    assert (await settle(runtime, task["id"]))["status"] == "completed"
    task = await runtime.invoke(
        "task.submit",
        {
            "request_id": "enhance",
            "application_id": "core",
            "kind": "local",
            "capability": "video_enhancement",
            "model": "ffmpeg-video-sharpen",
            "resource_ids": [resource["id"]],
        },
    )
    result = await settle(runtime, task["id"])
    assert result["status"] == "completed", result
    output = result["result"]["resources"][0]
    assert output["byte_size"] > 100 and runtime.resources.path(output["id"])[0].is_file()


def test_harness_proxy_environment_preserves_proxy_and_normalizes_ipv6():
    from dsh_core_worker.network import configure_environment

    environ = {
        "NO_PROXY": "example.com,localhost,127.0.0.1,::1,[::1]",
        "HTTP_PROXY": "http://proxy.example:8080",
    }
    configure_environment(environ)
    assert environ["NO_PROXY"] == "example.com,localhost,127.0.0.1,::1"
    assert environ["HTTP_PROXY"] == "http://proxy.example:8080"


async def test_chunked_upload_replay_resume_and_integrity(runtime):
    data = b"abcd" * 400000
    start = await runtime.invoke(
        "resource.upload.begin",
        {"name": "clip.mp4", "mime_type": "video/mp4", "byte_size": len(data)},
    )
    part = {
        "id": start["id"],
        "offset": 0,
        "data_base64": base64.b64encode(data[:1000000]).decode(),
    }
    first = await runtime.invoke("resource.upload.append", part)
    assert first == await runtime.invoke("resource.upload.append", part)
    assert (await runtime.invoke("resource.upload.status", {"id": start["id"]}))[
        "offset"
    ] == 1000000
    with pytest.raises(CoreError):
        await runtime.invoke("resource.upload.finish", {"id": start["id"]})
    await runtime.invoke(
        "resource.upload.append",
        {
            "id": start["id"],
            "offset": 1000000,
            "data_base64": base64.b64encode(data[1000000:]).decode(),
        },
    )
    resource = await runtime.invoke("resource.upload.finish", {"id": start["id"]})
    output = b""
    offset = 0
    while True:
        part = await runtime.invoke("resource.chunk", {"id": resource["id"], "offset": offset})
        output += base64.b64decode(part["data_base64"])
        offset = part["next_offset"]
        if part["eof"]:
            break
    assert output == data
    with pytest.raises(CoreError):
        await runtime.invoke("resource.chunk", {"id": resource["id"], "offset": -1})


async def test_default_replay_uses_original_receipt_after_configuration_change(runtime):
    await runtime.invoke(
        "config.save",
        {
            "key": "defaults",
            "revision": 0,
            "value": {"chat": {"connection_id": "harness:one", "model": "one"}},
        },
    )
    request = {"request_id": "default-idempotent", "application_id": "core", "prompt": "hello"}
    first = await runtime.invoke("task.submit", request)
    await runtime.invoke(
        "config.save",
        {
            "key": "defaults",
            "revision": 1,
            "value": {"chat": {"connection_id": "harness:two", "model": "two"}},
        },
    )
    replay = await runtime.invoke("task.submit", request)
    assert replay["id"] == first["id"] and replay["request"]["model"] == "one"


async def test_runtime_dispatches_agent_snapshot_without_model_fields(runtime):
    runtime.agents.connection = lambda _: (
        __import__("dsh_core_worker.contracts", fromlist=["AgentConnection"]).AgentConnection(
            id="codex"
        ),
        "/fake/codex",
    )
    seen = []

    async def execute(request, emit, scratch, connection=None):
        seen.append(connection)
        return {"agent": "codex"}

    runtime.agents.execute = execute
    task = await runtime.invoke(
        "task.submit",
        {
            "request_id": "agent",
            "application_id": "core",
            "kind": "agent",
            "connection_id": "codex",
        },
    )
    assert (await settle(runtime, task["id"]))["status"] == "completed"
    assert seen[0]["executable"] == "/fake/codex" and "payload" not in seen[0]
