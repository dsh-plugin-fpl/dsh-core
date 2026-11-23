"""An optional network bridge owns callbacks, never the Core runtime lifecycle."""

import asyncio
from contextlib import suppress
from uuid import uuid4

from .applications import ApplicationRegistration
from .contracts import CoreError


class Bridge:
    def __init__(self, identity, socket):
        self.id, self.socket = identity, socket
        self.pending, self.applications, self.models, self.requests = {}, {}, [], {}
        self.integration_adapters = []
        self.lock = asyncio.Lock()

    async def send(self, body):
        async with self.lock:
            await self.socket.send_json({"jsonrpc": "2.0", **body})

    async def call(self, method, params):
        identity = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        try:
            await self.send({"id": identity, "method": method, "params": params})
            async with asyncio.timeout(3600):
                return await future
        except asyncio.CancelledError:
            with suppress(RuntimeError, OSError):
                await self.send({"method": "host.cancel", "params": {"id": identity}})
            raise
        finally:
            self.pending.pop(identity, None)

    def disconnect(self):
        for future in self.pending.values():
            if not future.done():
                future.set_exception(
                    CoreError("DSH 连接已断开，已有输出已保留", "bridge_disconnected")
                )
        for request in self.requests.values():
            request.cancel()


class Bridges:
    def __init__(self, access):
        self.access, self.online = access, {}
        self.runtime = None
        self.credentials = None
        self.catalog = None
        self.model_catalog_view = None

    def application(self, identity):
        for bridge in self.online.values():
            for original in bridge.applications:
                if self.scope(bridge.id, original) == identity:
                    return bridge, original
        raise CoreError("应用连接已断开", "bridge_disconnected")

    @staticmethod
    def scope(identity, application):
        return f"b-{identity}-{application}"

    async def host(self, method, params):
        if method == "credential.resolve":
            return self.credentials.secret(params["ref"])
        if method == "application.ready":
            return True  # Durable jobs need a live bridge only when invoking a callback.
        if method == "tool.call":
            bridge, original = self.application(params["application_id"])
            declared = next(
                (
                    t
                    for t in bridge.applications[original].get("tools", [])
                    if t["name"] == params["name"]
                ),
                None,
            )
            if declared is None or declared.get("inputSchema") != params.get("input_schema"):
                raise CoreError("应用工具已更新，请核对后重新提交任务", "tool_changed")
            return await bridge.call(method, {**params, "application_id": original})
        if method == "harness.generate":
            request = params["request"]
            parts = request["connection_id"].split(":", 2)
            if len(parts) != 3 or parts[1] not in self.online:
                raise CoreError("DSH 模型连接已断开", "bridge_disconnected")
            request = {**request, "connection_id": "harness:" + parts[2]}
            return await self.online[parts[1]].call(method, {**params, "request": request})
        raise CoreError("宿主回调未登记", "host_method")

    def model_catalog(self):
        return [
            {**provider, "id": f"harness:{bridge.id}:{provider['id']}"}
            for bridge in self.online.values()
            for provider in bridge.models
        ]

    async def invoke(self, bridge, method, params):
        runtime = self.runtime
        if hasattr(self.access,"authorize_bridge"): self.access.authorize_bridge(bridge.id)
        if hasattr(self.access,'profile') and self.access.profile()['role']!='admin':
            if method in {'agent.detect','local.manage'} or (method=='task.submit' and params.get('kind')=='agent'):
                raise CoreError('此操作需要本机管理员权限','forbidden')
        if method=='model.options':
            if params.get('application_id') not in bridge.applications:raise CoreError('请先登记应用','application_unregistered')
            from .model_options import application_options
            return application_options(self.model_catalog_view)
        if method=='model.transcription-parameters':
            if params.get('application_id') not in bridge.applications:raise CoreError('请先登记应用','application_unregistered')
            from .model_options import transcription_parameters
            return transcription_parameters(self.model_catalog_view,params)
        if method=='task.find':
            application=params.get('application_id')
            if application not in bridge.applications:raise CoreError('请先登记应用','application_unregistered')
            scope=self.scope(bridge.id,application)
            for task in runtime.store.tasks():
                request=task['request']
                if request['application_id']==scope and request['session_id']==params['session_id'] and request['request_id']==params['request_id']:return task
            return None
        if method.startswith('external.'):
            application=params.get('application_id')
            if application not in bridge.applications:raise CoreError('请先登记应用','application_unregistered')
            return await self.external_sync.invoke(method,params,application)
        if method == "hello":
            if params.get("protocol") != 1:
                raise CoreError("连接协议版本不兼容", "protocol_version")
            return {
                **await runtime.invoke("hello", {"protocol": 1}),
                "bridge_id": bridge.id,
                "account": self.access.profile() if hasattr(self.access,'profile') else None,
                "features": ["application-catalog", "integration-transfers", "generation-catalog", "account-identity", "application-model-options", "application-transcription-parameters", "article-sync"],
            }
        if method == "capabilities":
            connections = [
                {k: row[k] for k in ("id", "name", "kind", "enabled", "models")}
                for row in runtime.store.config("connections", [])["value"]
            ]
            connections.extend(
                {**row, "kind": "harness", "enabled": True} for row in self.model_catalog()
            )
            from .generation_catalog import generation_options
            configured = bool(runtime.store.config('settings', {})['value'].get('temporary_upload_url'))
            providers = self.model_catalog_view.view()['providers']
            return {"connections": connections, "defaults": runtime.store.config("defaults", {}),
                    'generation_options': generation_options(providers,upload_configured=configured),
                    'temporary_upload_configured':configured}
        if method == "integrations.publish":
            from .integrations.host_contract import AdapterDescriptor
            rows=[AdapterDescriptor.model_validate(row).model_dump() for row in params.get("adapters",[])]
            if len(rows)>50 or len({r["id"] for r in rows})!=len(rows):raise CoreError("集成适配器目录无效")
            if any(r["application_id"] not in bridge.applications for r in rows):raise CoreError("请先登记应用")
            bridge.integration_adapters=rows
            return {"accepted":True}
        if method == "models.publish":
            if not isinstance(params.get("providers"), list) or len(params["providers"]) > 200:
                raise CoreError("模型目录无效")
            bridge.models = params["providers"]
            return {"accepted": True}
        if method == "applications.sync":
            registrations = params.get("applications", [])
            if not isinstance(registrations, list):
                raise CoreError("应用目录无效")
            rows = self.catalog.replace(bridge.id, registrations)
            bridge.applications = rows
            return {"synchronized": True}
        if method == "application.register":
            row = ApplicationRegistration.model_validate(params).model_dump()
            rows = {**bridge.applications, row["id"]: row}
            self.catalog.replace(bridge.id, list(rows.values()))
            bridge.applications = rows
            return {"registered": True}
        if method == "application.unregister":
            rows = {k: v for k, v in bridge.applications.items() if k != params["id"]}
            self.catalog.replace(bridge.id, list(rows.values()))
            bridge.applications = rows
            return {"registered": False}
        if method == "task.submit":
            original = params.get("application_id", "core")
            application = bridge.applications.get(original)
            if not application:
                raise CoreError("请先登记调用方应用", "application_unregistered")
            request = {
                **params,
                "application_id": self.scope(bridge.id, original),
                "tools": application.get("tools", []),
            }
            if request.get("connection_id", "").startswith("harness:"):
                parts = request["connection_id"].split(":", 2)
                if len(parts) == 2:
                    request["connection_id"] = f"harness:{bridge.id}:{parts[1]}"
            return await runtime.invoke(method, request)
        if method in (
            "task.get",
            "task.events",
            "task.cancel",
            "task.resume",
            "voice.command",
            "voice.events",
        ):
            task = runtime.store.task(params["id"])
            prefix = f"b-{bridge.id}-"
            if not task["request"]["application_id"].startswith(prefix):
                raise CoreError("任务不存在", "not_found")
            return await runtime.invoke(method, params)
        if method == "host.event":
            task = runtime.store.task(params["id"])
            if task["request"]["connection_id"].split(":")[1:2] != [bridge.id]:
                raise CoreError("任务不存在", "not_found")
            return await runtime.invoke(method, params)
        if method in ("resource.put", "resource.upload.begin"):
            original = params.get("application_id", "core")
            if original not in bridge.applications:
                raise CoreError("请先登记调用方应用", "application_unregistered")
            return await runtime.invoke(
                method, {**params, "application_id": self.scope(bridge.id, original)}
            )
        if method in ("resource.read", "resource.chunk"):
            info = runtime.resources.describe(params["id"])
            if info["application_id"] != "core" and not info["application_id"].startswith(
                f"b-{bridge.id}-"
            ):
                raise CoreError("资源不存在", "not_found")
            return await runtime.invoke(method, params)
        if method.startswith("resource.upload."):
            metadata = runtime.resources.upload("status", params)
            if not metadata["application_id"].startswith(f"b-{bridge.id}-"):
                raise CoreError("上传不存在", "not_found")
            return await runtime.invoke(method, params)
        raise CoreError("此连接不允许调用该操作", "forbidden")
