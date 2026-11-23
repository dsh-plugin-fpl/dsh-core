"""Five CLI transports with explicit task tools and existing CLI sign-in."""

import asyncio
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .contracts import AgentConnection, CoreError
from .extracted.host.agent_execution.cli.catalog import inspect_executable
from .extracted.host.agent_execution.cli.conversation import (
    claude,
    codex,
    codex_models,
    probe_codex,
)
from .extracted.host.agent_execution.cli.model_catalog import discover_models
from .extracted.host.agent_execution.cli.native import converse
from .extracted.host.agent_execution.cli.tool_relay import ToolRelay


@contextmanager
def codex_profile(root):
    original = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    with tempfile.TemporaryDirectory(prefix="codex-", dir=root) as name:
        target = Path(name)
        auth = original / "auth.json"
        if not auth.is_file() or auth.is_symlink():
            raise CoreError("请先在 Codex CLI 登录", "not_authenticated")
        destination = target / "auth.json"
        destination.write_bytes(auth.read_bytes())
        destination.chmod(0o600)
        yield target


class Agents:
    def __init__(self, store, host):
        self.store, self.host = store, host
        self.root = store.root / "agent-runs"
        self.root.mkdir(mode=0o700, exist_ok=True)

    def connection(self, identity):
        stored = self.store.config("agents", {})["value"].get(identity, {"id": identity})
        value = AgentConnection.model_validate(stored)
        executable = value.executable or shutil.which(
            {"claude-code": "claude"}.get(identity, identity)
        )
        if not value.enabled:
            raise CoreError("该智能体连接已停用", "connection_disabled")
        if not executable:
            raise CoreError("未找到智能体 CLI", "not_installed")
        return value, str(Path(executable).resolve())

    async def detect(self, identity, models=True):
        _value, executable = self.connection(identity)
        async with asyncio.timeout(30):
            if identity == "codex":
                with codex_profile(self.root) as profile:
                    result = await inspect_executable(identity, executable, codex_profile=profile)
                    if result["available"] and models:
                        async with probe_codex(executable, profile) as request:
                            result["models"] = await codex_models(request)
            else:
                result = await inspect_executable(identity, executable)
                if result["available"] and models:
                    result["models"] = await discover_models(identity, executable)
        return result

    async def execute(self, request, emit, scratch, connection=None):
        if connection is None:
            connection, executable = self.connection(request.connection_id)
        else:
            connection = AgentConnection.model_validate(connection)
            executable = connection.executable
        tools = []
        for tool in request.tools:
            tools.append(
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "inputSchema": tool.get("inputSchema", tool.get("parameters_schema", {})),
                }
            )

        async def call(name, args):
            if name not in {t["name"] for t in tools}:
                raise CoreError("任务未注册该工具", "tool_unavailable")
            from jsonschema import validate

            validate(args, next(t["inputSchema"] for t in tools if t["name"] == name))
            return await self.host(
                "tool.call",
                {
                    "application_id": request.application_id,
                    "session_id": request.session_id,
                    "name": name,
                    "arguments": args,
                    "input_schema": next(t["inputSchema"] for t in tools if t["name"] == name),
                },
            )

        relay = ToolRelay(tools, call, emit)
        config = await relay.start()
        model = request.model or connection.model
        options = {**connection.options, **request.parameters}

        async def observed(kind, value):
            await emit(kind, value)

        try:
            if connection.id == "codex":
                with codex_profile(self.root) as profile:
                    definitions = [
                        {
                            "name": t["name"],
                            "description": t["description"],
                            "inputSchema": t["inputSchema"],
                        }
                        for t in tools
                    ]
                    await codex(
                        executable,
                        profile,
                        model,
                        request.prompt,
                        definitions,
                        call,
                        observed,
                        reasoning_effort=options.get("reasoning_effort"),
                        service_tier=options.get("service_tier"),
                    )
            elif connection.id == "claude-code":
                await claude(
                    executable,
                    scratch,
                    model,
                    request.prompt,
                    config,
                    observed,
                    reasoning_effort=options.get("reasoning_effort"),
                )
            else:
                await converse(
                    connection.id,
                    executable,
                    scratch,
                    model,
                    request.prompt,
                    config,
                    observed,
                    reasoning_effort=options.get("reasoning_effort"),
                    model_variant=options.get("model_variant"),
                    harness_effort=options.get("harness_effort"),
                )
        finally:
            await relay.close()
        return {"agent": connection.id, "model": model}
