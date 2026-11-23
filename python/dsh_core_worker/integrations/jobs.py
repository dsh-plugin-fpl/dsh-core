"""Integration execution receipts share Core's durable run history and cancellation."""

import asyncio
import json
from dataclasses import asdict, is_dataclass
from uuid import uuid4

from ..contracts import CoreError, Execution


class IntegrationJobs:
    def __init__(self, runtime):
        self.runtime = runtime
        self.jobs = {}

    async def live(self):
        if self.runtime.closing:
            raise CoreError("Core 正在退出")

    async def run(self, snapshot, inputs, operation, *, kind=None):
        identity = snapshot.id
        group = kind or ("mcp" if hasattr(snapshot, "transport") else "tool_api")
        request = Execution(
            request_id=str(uuid4()),
            application_id="core",
            kind="integration",
            capability="tools",
            connection_id=identity,
            model=group,
            prompt=snapshot.name,
            parameters={"operation": "validate", "revision": snapshot.revision},
        )
        return await self.record(request, operation, persist_result=False)

    async def record(self, request, operation, *, persist_result=True, receipt=False):
        store = self.runtime.store
        task, created = store.admit(request, {"kind": "integration"})
        if not created:
            return task if receipt else task.get("result")
        identity = task["id"]
        store.update(identity, status="running")

        async def execute():
            try:
                value = await operation()
                output = (
                    asdict(value)
                    if is_dataclass(value)
                    else value.model_dump(mode="json")
                    if hasattr(value, "model_dump")
                    else value
                )
                result = output if persist_result else {"summary": "集成验证已完成"}
                # Only caller-redacted results are persisted; validation wire bodies stay out of history.
                json.dumps(result)
                store.update(identity, status="completed", progress=100, result=result)
                return value
            except asyncio.CancelledError:
                if store.task(identity)["status"] != "cancelled":
                    store.update(
                        identity,
                        status="interrupted",
                        error={
                            "code": "unknown_result",
                            "message": "执行已中断，外部结果需要核对；不会自动重提",
                        },
                    )
                raise
            except BaseException:
                store.update(
                    identity,
                    status="failed",
                    error={
                        "code": "integration_failed",
                        "message": "集成调用失败，请查看集成检查结果",
                    },
                )
                raise
            finally:
                self.jobs.pop(identity, None)
                self.runtime.running.pop(identity, None)

        run = asyncio.create_task(execute())
        self.jobs[identity] = run
        self.runtime.running[identity] = run
        try:
            result = await asyncio.shield(run)
        except Exception:
            if not receipt:
                raise
            result = None
        return store.task(identity) if receipt else result

    async def close(self):
        jobs = list(self.jobs.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

    async def list_pending(self, owner_user_id, *, offset=0):
        from .contracts.catalog import ValidationAttemptListOut, ValidationAttemptOut

        rows = [
            t
            for t in self.runtime.store.tasks()
            if t["request"]["kind"] == "integration"
            and t["request"]["model"] in ("mcp", "tool_api")
            and t["status"] in ("interrupted", "recoverable")
        ]
        return ValidationAttemptListOut(
            items=[
                ValidationAttemptOut(
                    id=t["id"],
                    kind="mcp.validate" if t["request"]["model"] == "mcp" else "tool.validate",
                    connection_id=t["request"]["connection_id"],
                    status="unknown",
                    released=False,
                    created_at=t["created_at"],
                )
                for t in rows[offset : offset + 100]
            ],
            has_more=len(rows) > offset + 100,
        )

    async def reconcile(self, owner_user_id, identity):
        from .contracts.catalog import ValidationReconcileOut

        task = self.runtime.store.task(identity)
        if task["request"]["kind"] != "integration":
            raise CoreError("验证记录不存在", "not_found")
        if task["status"] not in ("completed", "failed", "cancelled"):
            raise CoreError("尚无可信结束回执；不会重新执行验证", "task_busy")
        return ValidationReconcileOut(id=identity, status="completed", released=True)
