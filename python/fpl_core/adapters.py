"""Reusable product adapters. Product identity and business assertions stay owned by it."""

import asyncio
from contextlib import asynccontextmanager
import json

from fastapi import HTTPException

from .host import CoreHost, CoreOptions
from dsh_core_worker.contracts import CoreError
from dsh_core_worker.private_files import atomic_private


class EmbeddedIdentityMixin:
    def __init__(self, root, host):
        super().__init__(root)
        self.host = host

    def login_url(self, state):
        from urllib.parse import urlencode
        return "/__desktop/login?" + urlencode({"state": state})

    async def call(self, path, body):
        response = await self.host.identity_request(path, body, self.config()['token'])
        if response.status_code != 200:
            raise HTTPException(response.status_code, response.json())
        return response.json()

    async def directory(self, actor, query):
        # Business ownership retains its original IDs. Translate both query and
        # result at the adapter boundary; do not leak Core IDs into owner tables.
        result = await super().directory(actor, {
            **query, 'ids':[self.canonical_id(identity) for identity in query.get('ids', [])],
        })

        def local(profile):
            return {key:value for key,value in self.local_profile(profile).items()
                    if key != 'core_account_id'}

        return {**result, 'caller':local(result['caller']),
                'profiles':[local(profile) for profile in result['profiles']]}


class _Port:
    def __init__(self, host, account, error_type):
        self.host, self.account, self.error_type = host, account, error_type
        self.application_id = host.options.application_id
        self.connected = asyncio.Event()
        self.connected.set()

    async def call(self, method, params, *, timeout=320):
        try:
            async with asyncio.timeout(timeout):
                return await self.host.call_account(self.account, method, params)
        except CoreError as error:
            failure = self.error_type(str(error))
            failure.code = error.code
            raise failure from error
        except TimeoutError as error:
            raise self.error_type('公共能力调用结果未确认，请按原请求核对。') from error


class EmbeddedPool:
    def __init__(self, root, access, host, *, error_type=CoreError):
        self.root, self.access, self.host = root, access, host
        self.error_type = error_type
        self.ports = {}

    def port(self, owner):
        if owner not in self.ports:
            self.ports[owner] = _Port(self.host, owner, self.error_type)
        return self.ports[owner]

    def start(self, owner):
        return self.port(owner)

    def owners(self):
        audience = self.host.options.identity_id or self.host.options.application_id
        return [p['id'] for p in self.host.accounts.profiles()
                if p['active'] and self.host.accounts.has_grant(p['id'], audience)]

    async def for_actor(self, actor):
        if not await self.access.active_async(actor):
            raise HTTPException(401, 'account_revoked')
        return self.port(self.access.canonical_id(actor.user_id))

    async def call(self, actor, method, params, **options):
        return await (await self.for_actor(actor)).call(method, params, **options)

    @asynccontextmanager
    async def lifespan(self):
        try:
            yield self
        finally:
            for port in self.ports.values():
                port.connected.clear()


def prepare_product(root, port, *, application_id, identity_id, title, initialize, owner_id):
    """Prepare product-owned state using the provider's own initializer and owner view."""
    identity = root / 'identity.json'
    if identity.exists():
        old = json.loads(identity.read_text())
        if old.get('mode') != 'embedded':
            raise ValueError('此目录仍连接外部 Core，请先使用迁移入口创建桌面副本。')
    else:
        old = {}
    initialize(root)
    host = CoreHost(CoreOptions(application_id, title, root / 'embedded-core',
        identity_id=identity_id, origin=f'http://127.0.0.1:{port}'))
    value = host.provision_product(credential=old.get('token'), relocate=bool(old))
    value.update(mode='embedded', application_origin=value['origin'],
        legacy_owner_id=old.get('legacy_owner_id', owner_id(root)))
    atomic_private(identity, json.dumps(value).encode())
    return host
