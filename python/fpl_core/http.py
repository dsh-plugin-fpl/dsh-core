"""Compose Core HTTP routes behind the product's authenticated HTTP boundary."""

from http.cookies import SimpleCookie
import hashlib
from starlette.responses import JSONResponse

from .host import CoreHost
from dsh_core_worker.contracts import CoreError


class CoreHttp:
    def __init__(self, host: CoreHost, session_cookie: str, prefix="/v1/core"):
        self.host, self.cookie, self.prefix = host, session_cookie, prefix
        self.sessions = {}

    async def __call__(self, scope, receive, send):
        cookies = SimpleCookie()
        cookies.load(dict(scope["headers"]).get(b"cookie", b"").decode())
        token = cookies[self.cookie].value if self.cookie in cookies else ""
        try:
            profile = self.host.profile(token)
        except CoreError:
            return await JSONResponse({"code": "unauthorized"}, 401)(scope, receive, send)
        # Product middleware verifies Origin and CSRF before reaching this adapter.
        path = "/v1" + scope["path"][len(self.prefix):]
        if path == '/v1/storage' or path.startswith('/v1/storage/'):
            return await JSONResponse({'code':'product_storage_managed'}, 403)(scope, receive, send)
        if path.startswith(("/v1/session", "/v1/identity/login", "/v1/identity/logout")):
            return await JSONResponse({"code": "product_session_required"}, 403)(scope, receive, send)
        key = hashlib.sha256(token.encode()).hexdigest()
        pair = self.sessions.get(key)
        if pair is None or not self.host.accounts.session(pair[0]):
            pair = self.host.accounts.issue(profile["id"])
            self.sessions[key] = pair
        headers = [(k, v) for k, v in scope["headers"] if k.lower() not in {
            b"cookie", b"x-core-csrf", b"origin", b"host", b"authorization"}]
        headers.extend([(b"cookie", ("core_session=" + pair[0]).encode()),
            (b"x-core-csrf", pair[1].encode()), (b"origin", self.host.options.origin.encode()),
            (b"host", self.host.options.origin.split("://", 1)[1].encode())])
        forwarded = {**scope, "path": path, "raw_path": path.encode(), "root_path": "", "headers": headers}

        async def safe_send(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [(k, v) for k, v in message["headers"] if k.lower() != b"set-cookie"]}
            await send(message)
        await self.host.app(forwarded, receive, safe_send)
