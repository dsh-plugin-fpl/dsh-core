"""Run one bounded live CLI smoke task through the independent worker runtime."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from dsh_core_worker.runtime import Runtime


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--agent", default="codex", choices=["codex", "claude-code", "opencode", "dsh", "pi"]
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--root", default=".runtime/live-agent")
    args = parser.parse_args()

    async def host(*_args):
        raise RuntimeError("Smoke task has no tools")

    async def notify(*_args):
        pass

    runtime = Runtime(Path(args.root), host, notify)
    try:
        task = await runtime.invoke(
            "task.submit",
            {
                "request_id": str(uuid4()),
                "application_id": "core",
                "session_id": "acceptance",
                "kind": "agent",
                "connection_id": args.agent,
                "model": args.model,
                "prompt": "Reply with DSH_CORE_OK only. Do not call tools.",
            },
        )
        async with asyncio.timeout(90):
            while True:
                task = runtime.store.task(task["id"])
                if task["status"] in ("completed", "failed", "interrupted", "cancelled"):
                    break
                await asyncio.sleep(0.25)
        result = {k: task[k] for k in ("status", "text", "error")}
        print(json.dumps(result, ensure_ascii=False))
        if task["status"] != "completed" or "DSH_CORE_OK" not in task["text"]:
            raise SystemExit(1)
    finally:
        await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
