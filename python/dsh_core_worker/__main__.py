"""Bounded JSON-RPC 2.0 worker; stdout is exclusively the transport."""

import argparse
import asyncio
import json
import sys
from uuid import uuid4

from .contracts import CoreError
from .runtime import Runtime


class Server:
    def __init__(self):
        self.pending = {}
        self.requests = {}
        self.write_lock = asyncio.Lock()

    async def send(self, message):
        data = json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False, allow_nan=False) + "\n"
        async with self.write_lock:
            sys.stdout.write(data)
            sys.stdout.flush()

    async def notify(self, method, params):
        await self.send({"method": method, "params": params})

    async def host(self, method, params):
        identity = "host:" + str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        try:
            await self.send({"id": identity, "method": method, "params": params})
            async with asyncio.timeout(3600):
                return await future
        except asyncio.CancelledError:
            await self.notify("host.cancel", {"id": identity})
            raise
        finally:
            self.pending.pop(identity, None)

    async def handle(self, message, runtime):
        identity = message.get("id")
        try:
            result = await runtime.invoke(message["method"], message.get("params", {}))
            await self.send({"id": identity, "result": result})
        except Exception as error:  # noqa: BLE001 — RPC boundary returns sanitized failures
            detail = {
                "code": getattr(error, "code", "internal_error"),
                "message": str(error) if isinstance(error, CoreError) else "执行层请求失败",
            }
            await self.send(
                {
                    "id": identity,
                    "error": {"code": -32000, "message": detail["message"], "data": detail},
                }
            )
        finally:
            self.requests.pop(identity, None)

    async def run(self, root):
        runtime = Runtime(root, self.host, self.notify)
        reader = asyncio.StreamReader(limit=32 * 1024 * 1024)
        protocol = asyncio.StreamReaderProtocol(reader)
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: protocol, sys.stdin
        )
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                        raise ValueError()
                    identity = message.get("id")
                    if "method" not in message:
                        future = self.pending.get(identity)
                        if future and not future.done():
                            if "error" in message:
                                future.set_exception(CoreError("宿主能力调用失败", "host_error"))
                            else:
                                future.set_result(message.get("result"))
                    elif message["method"] == "$/cancelRequest":
                        request = self.requests.get(message.get("params", {}).get("id"))
                        if request:
                            request.cancel()
                    elif identity is not None:
                        if len(self.requests) >= 128:
                            raise CoreError("请求队列已满", "busy")
                        if identity in self.requests:
                            raise ValueError()
                        self.requests[identity] = asyncio.create_task(self.handle(message, runtime))
                except (ValueError, KeyError, CoreError):
                    await self.send(
                        {"id": None, "error": {"code": -32600, "message": "JSON-RPC 请求无效"}}
                    )
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.cancel()
            pending = list(self.requests.values())
            for request in pending:
                request.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await runtime.close()
            transport.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    from .network import configure_environment

    configure_environment()
    try:
        asyncio.run(Server().run(args.root))
    except (CoreError, OSError):
        sys.stderr.write("dsh-core: 无法启动此配置的执行进程\n")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
