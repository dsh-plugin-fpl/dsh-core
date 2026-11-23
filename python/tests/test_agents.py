import asyncio
import sys
from pathlib import Path

import pytest
from dsh_core_worker.agents import Agents
from dsh_core_worker.contracts import Execution
from dsh_core_worker.store import Store


@pytest.fixture
async def agents(tmp_path, monkeypatch):
    source = Path(__file__).parent / "fixtures/native_code_agents.py"
    executable = tmp_path / "fake-native"
    executable.write_text(f"#!{sys.executable}\n" + source.read_text())
    executable.chmod(0o700)
    home = tmp_path / "dsh-home"
    home.mkdir()
    (home / ".credentials.yaml").write_text("fixture: not-a-real-credential")
    monkeypatch.setenv("DSH_HOME", str(home))
    pi = tmp_path / "pi-home"
    pi.mkdir()
    (pi / "auth.json").write_text('{"fixture":{"type":"api_key","key":"fixture"}}')
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(pi))
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-inherited")
    store = Store(tmp_path / "profile")
    store.save_config(
        "agents",
        {kind: {"id": kind, "executable": str(executable)} for kind in ("opencode", "dsh", "pi")},
        0,
    )
    calls = []

    async def host(method, params):
        assert method == "tool.call"
        calls.append(params)
        return {"result": "fixture"}

    value = Agents(store, host)
    yield value, calls, tmp_path
    store.close()


@pytest.mark.parametrize("kind", ["opencode", "dsh", "pi"])
async def test_native_tools_isolation_and_streaming(agents, kind):
    agent, calls, root = agents
    events = []

    async def emit(kind, value):
        events.append((kind, value))

    scratch = root / "run"
    scratch.mkdir()
    request = Execution(
        request_id="native",
        application_id="example",
        kind="agent",
        connection_id=kind,
        model="fixture/model",
        prompt="Inspect",
        parameters={"reasoning_effort": "high"} if kind == "pi" else {},
        tools=[
            {
                "name": "operations",
                "description": "Read operations",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    )
    await agent.execute(request, emit, scratch)
    assert calls and calls[0]["name"] == "operations"
    assert any(
        kind == "assistant_delta" and "已核对项目" in value["text"] for kind, value in events
    )


async def test_native_cancellation_reaps_process(agents):
    agents, _, root = agents
    import os

    from dsh_core_worker.extracted.host.agent_execution.cli.native import converse

    started = asyncio.get_running_loop().create_future()

    async def emit(kind, value):
        if kind == "assistant_delta" and not started.done():
            started.set_result(int(value["text"]))

    _, executable = agents.connection("opencode")
    task = asyncio.create_task(converse("opencode", executable, root, "", "SLOW_NATIVE", {}, emit))
    try:
        done, _ = await asyncio.wait(
            {started, task}, timeout=10, return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            await task
        assert started in done
        pid = started.result()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("kind", ["codex", "claude-code"])
async def test_official_cli_tools_and_streaming(tmp_path, monkeypatch, kind):
    import runpy

    fake_cli = runpy.run_path(str(Path(__file__).parent / "fixtures/codex_claude.py"))["fake_cli"]
    executable = fake_cli(tmp_path)
    home = tmp_path / "codex-home"
    home.mkdir(mode=0o700)
    (home / "auth.json").write_text(
        '{"tokens":{"access_token":"fixture","refresh_token":"fixture","id_token":"fixture","account_id":"fixture"}}'
    )
    (home / "auth.json").chmod(0o600)
    monkeypatch.setenv("CODEX_HOME", str(home))
    store = Store(tmp_path / "profile")
    store.save_config("agents", {kind: {"id": kind, "executable": executable}}, 0)
    calls = []

    async def host(method, params):
        calls.append(params)
        return {"ok": True}

    agent = Agents(store, host)
    events = []

    async def emit(kind, value):
        events.append((kind, value))

    scratch = tmp_path / "run"
    scratch.mkdir()
    request = Execution(
        request_id="test",
        application_id="example",
        kind="agent",
        connection_id=kind,
        prompt="Read project",
        tools=[
            {
                "name": "operations",
                "description": "Read",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    )
    try:
        await agent.execute(request, emit, scratch)
    finally:
        store.close()
    assert calls and calls[0]["name"] == "operations"
    assert any(
        kind == "assistant_delta" and "已核对项目" in value["text"] for kind, value in events
    )
