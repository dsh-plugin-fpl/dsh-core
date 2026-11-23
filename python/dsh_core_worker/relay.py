"""MCP stdio carrier for the run-scoped Unix-socket tool relay."""

import asyncio
import json
import os
import sys


async def main():
    definitions = json.loads(os.environ.get("FPL_TOOL_DEFINITIONS", "[]"))
    while line := await asyncio.to_thread(sys.stdin.buffer.readline):
        message = {}
        try:
            message = json.loads(line)
            method = message.get("method")
            if "id" not in message:
                continue
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "dsh-core-tools", "version": "0.1.0"},
                }
            elif method == "tools/list":
                result = {"tools": definitions}
            elif method == "tools/call":
                params = message["params"]
                reader, writer = await asyncio.open_unix_connection(
                    os.environ["FPL_TOOL_SOCKET"], limit=2 * 1024 * 1024
                )
                try:
                    writer.write(
                        json.dumps(
                            {
                                "token": os.environ["FPL_TOOL_TOKEN"],
                                "name": params["name"],
                                "arguments": params.get("arguments", {}),
                            }
                        ).encode()
                        + b"\n"
                    )
                    await writer.drain()
                    async with asyncio.timeout(float(os.environ.get("FPL_TOOL_TIMEOUT", "120"))):
                        response = json.loads(await reader.readline())
                    if "error" in response:
                        result = {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(response["error"], ensure_ascii=False),
                                }
                            ],
                            "isError": True,
                        }
                    else:
                        value = response["result"]
                        result = {
                            "content": value
                            if isinstance(value, list)
                            else [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]
                        }
                finally:
                    writer.close()
                    await writer.wait_closed()
            elif method == "ping":
                result = {}
            else:
                raise ValueError("Unknown MCP method")
            response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        except Exception:  # noqa: BLE001 — MCP boundary returns a fixed error
            response = {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32602, "message": "MCP request failed"},
            }
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
