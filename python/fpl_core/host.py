"""Product-owned lifecycle and authenticated calls without a listening socket."""

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from dsh_core_worker.applications import ApplicationRegistration
from dsh_core_worker.bridges import Bridge
from dsh_core_worker.contracts import CoreError


@dataclass(frozen=True)
class CoreOptions:
    application_id: str
    name: str
    data_directory: Path
    identity_id: str | None = None
    origin: str = "http://127.0.0.1:9310"


class _LocalBridge(Bridge):
    def __init__(self, identity, callback):
        super().__init__(identity, self)
        self.callback = callback

    async def call(self, method, params):
        if self.callback is None:
            raise CoreError("产品未提供此宿主能力", "host_method")
        return await self.callback(method, params)

    async def close(self, **_):
        self.disconnect()


class CoreHost:
    """One product's embedded Core, with isolated per-account runtimes.

    The host authenticates every call with a Core session. Session issuance and
    account administration stay in the existing identity boundary. The ASGI app
    can be composed into the product; this class never starts an HTTP server.
    """

    def __init__(self, options: CoreOptions, *, callback: Callable[[str, dict], Awaitable] | None = None):
        ApplicationRegistration(id=options.application_id, name=options.name)
        self.options = options
        self.callback = callback
        self._app = None
        self._lifecycle = None
        self._connections = {}
        self._lock = asyncio.Lock()

    @property
    def app(self):
        if self._app is None:
            raise CoreError("公共能力尚未启动", "runtime_not_started")
        return self._app

    @property
    def accounts(self):
        return self.app.state.accounts

    def prepare(self):
        """Explicitly initialize product-owned state without starting workers."""
        if self._app is None:
            from dsh_core_worker.service import create_app

            self._app = create_app(self.options.data_directory, origin=self.options.origin)
        return self._app

    async def __aenter__(self):
        if self._lifecycle is not None:
            raise CoreError("公共能力已启动", "runtime_already_started")
        self.prepare()
        self._lifecycle = self._app.router.lifespan_context(self._app)
        try:
            await self._lifecycle.__aenter__()
        except BaseException:
            self._app = self._lifecycle = None
            raise
        return self

    async def __aexit__(self, *exc):
        try:
            return await self._lifecycle.__aexit__(*exc)
        finally:
            self._app = self._lifecycle = None
            self._connections.clear()

    def profile(self, session):
        current = self.accounts.session(session)
        if not current:
            audience = self.options.identity_id or self.options.application_id
            current = self.accounts.session(session, audience=audience)
            if current and not self.accounts.has_grant(current["account_id"], audience):
                current = None
        if not current:
            raise CoreError("会话无效或已撤销", "unauthorized")
        return self.accounts.account(current["account_id"])

    async def call(self, session: str, method: str, params: dict):
        return await self._call(self.profile(session), method, params)

    async def call_account(self, account_id: str, method: str, params: dict):
        """Trusted host background jobs only; never expose account_id as authentication."""
        profile = self.accounts.account(account_id)
        audience = self.options.identity_id or self.options.application_id
        if not profile["active"] or not self.accounts.has_grant(account_id, audience):
            raise CoreError("产品授权已撤销", "forbidden")
        return await self._call(profile, method, params)

    async def _call(self, profile, method, params):
        if self._lifecycle is None:
            raise CoreError("公共能力尚未启动", "runtime_not_started")
        async with self._lock:
            tenant = await self.app.state.account_services.ensure(profile)
            identity = profile["id"]
            if identity not in self._connections:
                # Stable scope survives restarts; never use an ephemeral connection ID.
                bridge_id = "local-" + hashlib.sha256(self.options.application_id.encode()).hexdigest()[:24]
                bridge = _LocalBridge(bridge_id, self.callback)
                bridges = tenant.state.bridges
                bridges.online[bridge_id] = bridge
                await bridges.invoke(bridge, "applications.sync", {"applications": [{
                    "id": self.options.application_id, "name": self.options.name, "tools": [],
                }]})
                self._connections[identity] = (bridges, bridge)
            bridges, bridge = self._connections[identity]
        supplied = params.get("application_id", self.options.application_id)
        if supplied != self.options.application_id:
            raise CoreError("产品标识与调用范围不一致", "forbidden")
        return await bridges.invoke(bridge, method, {**params, "application_id": supplied})

    def provision_product(self, *, credential=None, callback=None, relocate=False):
        """Trusted installation entrypoint; returns a private, product-local receipt."""
        self.prepare()
        identity = self.options.identity_id or self.options.application_id
        if relocate and credential is not None:
            self.accounts.relocate_client(identity, credential, self.options.origin)
        value = self.accounts.register_client(identity, self.options.origin,
            callback or "/apps/" + self.options.application_id, token=credential)
        self.accounts.grant(self.accounts.initial["id"], identity)
        return {**value, "account_id": self.accounts.initial["id"]}

    async def identity_request(self, path, body, credential):
        """Invoke the existing authenticated identity boundary in process."""
        import httpx

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                base_url=self.options.origin, trust_env=False) as client:
            return await client.post("/v1/identity/" + path,
                headers={"Authorization": "Bearer " + credential},
                json={"application_id": self.options.identity_id or self.options.application_id, **body})

    def local_product_session(self):
        """Trusted launcher bootstrap only; caller must enforce a one-use launch secret."""
        identity = self.options.identity_id or self.options.application_id
        return self.accounts.issue(self.accounts.initial["id"], audience=identity)[0]

    def active_tasks(self):
        """Count active public jobs across this product's instantiated accounts."""
        return sum(len(app.state.runtime.store.tasks(active=True))
                   for app in self.app.state.account_services.apps.values())

    def password_product_session(self, username, password):
        """Authenticate with the existing rate-limited identity store, for this product."""
        temporary, _ = self.accounts.login(username, password, bucket='product-login')
        try:
            profile = self.profile(temporary)
            identity = self.options.identity_id or self.options.application_id
            self.accounts.grant(profile['id'], identity)
            return self.accounts.issue(profile['id'], audience=identity)[0]
        finally:
            self.accounts.revoke(temporary)
