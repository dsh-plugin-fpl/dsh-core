from __future__ import annotations

from pathlib import Path
from typing import Any

from dsh_core_worker.integrations.contracts.mcp import DiscoveredTool, DiscoveryResult
from dsh_core_worker.integrations.dependencies import Settings
from dsh_core_worker.integrations.network import require_safe_integration_http_url


class OfficialMcpDiscoveryClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _stdio_command(self, command: str) -> Path:
        import shutil

        resolved = Path(shutil.which(command) or command).expanduser().resolve()
        if not resolved.is_file():
            raise ValueError("stdio 可执行文件不存在")
        return resolved

    @staticmethod
    def _result(client: Any, tools: list[Any]) -> DiscoveryResult:
        info = getattr(client, "server_info", None)
        return DiscoveryResult(
            server_name=str(getattr(info, "name", "") or ""),
            server_version=str(getattr(info, "version", "") or ""),
            protocol_version=str(getattr(client, "protocol_version", "") or ""),
            tools=tuple(
                DiscoveredTool(
                    name=str(tool.name),
                    description=str(getattr(tool, "description", "") or ""),
                    input_schema=dict(
                        getattr(tool, "input_schema", None)
                        or getattr(tool, "inputSchema", None)
                        or {}
                    ),
                )
                for tool in tools
            ),
        )

    @staticmethod
    async def _all_tools(client: Any) -> list[Any]:
        tools: list[Any] = []
        cursor: str | None = None
        while True:
            page = await client.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.next_cursor
            if not cursor:
                return tools

    async def discover(
        self,
        *,
        transport: str,
        url: str,
        command: str,
        arguments: list[str],
        bearer_token: str,
        environment: dict[str, str],
        cwd: str = "",
        headers: dict[str, str] | None = None,
        timeout_ms: int = 30000,
    ) -> DiscoveryResult:
        from mcp import Client

        if transport == "streamable_http":
            import httpx2
            from mcp.client.streamable_http import streamable_http_client

            safe_url = await require_safe_integration_http_url(url)
            headers = {
                **(headers or {}),
                **({"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}),
            }
            timeout = httpx2.Timeout(timeout_ms / 1000, connect=min(10.0, timeout_ms / 1000))
            async with (
                httpx2.AsyncClient(
                    headers=headers,
                    timeout=timeout,
                    follow_redirects=False,
                    trust_env=False,
                ) as http_client,
                Client(streamable_http_client(safe_url, http_client=http_client)) as client,
            ):
                return self._result(client, await self._all_tools(client))

        from mcp.client.stdio import StdioServerParameters, stdio_client

        executable = self._stdio_command(command)
        parameters = StdioServerParameters(
            command=str(executable),
            args=list(arguments),
            env=dict(environment),
            cwd=cwd or None,
        )
        async with Client(stdio_client(parameters)) as client:
            return self._result(client, await self._all_tools(client))

    async def call(
        self,
        *,
        transport: str,
        url: str,
        command: str,
        arguments: list[str],
        bearer_token: str,
        environment: dict[str, str],
        cwd: str = "",
        headers: dict[str, str] | None = None,
        timeout_ms: int = 30000,
        tool_name: str,
        tool_arguments: dict[str, Any],
    ) -> dict[str, Any]:
        from mcp import Client

        if transport == "streamable_http":
            import httpx2
            from mcp.client.streamable_http import streamable_http_client

            safe_url = await require_safe_integration_http_url(url)
            headers = {
                **(headers or {}),
                **({"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}),
            }
            async with (
                httpx2.AsyncClient(
                    headers=headers,
                    timeout=httpx2.Timeout(timeout_ms / 1000, connect=min(10.0, timeout_ms / 1000)),
                    follow_redirects=False,
                    trust_env=False,
                ) as http_client,
                Client(streamable_http_client(safe_url, http_client=http_client)) as client,
            ):
                result = await client.call_tool(tool_name, tool_arguments)
                return result.model_dump(mode="json")

        from mcp.client.stdio import StdioServerParameters, stdio_client

        executable = self._stdio_command(command)
        parameters = StdioServerParameters(
            command=str(executable), args=list(arguments), env=dict(environment), cwd=cwd or None
        )
        async with Client(stdio_client(parameters)) as client:
            result = await client.call_tool(tool_name, tool_arguments)
            return result.model_dump(mode="json")
