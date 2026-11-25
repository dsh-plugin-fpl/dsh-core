"""Unified account boundary dispatching into isolated account-owned Core runtimes."""

import asyncio
import secrets
from contextlib import AsyncExitStack, asynccontextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .access import Access
from .accounts import Accounts
from .contracts import CoreError
from .identity_contracts import (
    AccountList,
    AccountProfile,
    Application,
    ApplicationDirectory,
    ApplicationDirectoryResult,
    ApplicationRegistrationResult,
    CreateAccount,
    IdentityMe,
    Login,
    Password,
    RegisterAccount,
    UpdateAccount,
)


class AccountAccess(Access):
    def __init__(self, root, accounts, identity):
        super().__init__(root)
        self.accounts, self.identity = accounts, identity

    def session(self, value):
        result = self.accounts.session(value)
        return result if result and result["account_id"] == self.identity else None

    def exchange(self, value):
        old, _ = super().exchange(value)
        self.sessions.pop(self.digest(old), None)
        return self.accounts.issue(self.identity)

    def authorize_bridge(self, identity):
        self.accounts.authorize_bridge(self.identity, identity)

    def bridges(self):
        applications = self.accounts.bridge_applications(self.identity)
        return [
            {**row, "kind": "application", "application_id": applications[row["id"]]}
            if row["id"] in applications else row
            for row in super().bridges()
        ]

    def profile(self):
        return self.accounts.account(self.identity)


class AccountServices:
    def __init__(self, root, factory, web_root, origin):
        self.root, self.factory, self.web_root, self.origin = Path(root), factory, web_root, origin
        self.accounts = Accounts(self.root / "identity")
        self.apps = {}
        self.stack = None
        self.initial = self.application(self.accounts.initial)
        self.lock = asyncio.Lock()

    def application(self, profile):
        identity = profile["id"]
        if identity not in self.apps:
            root = self.root if profile["legacy"] else self.root / "accounts" / identity
            access = AccountAccess(root / "access", self.accounts, identity)
            self.apps[identity] = self.factory(
                root, self.web_root, self.origin, account_access=access
            )
        return self.apps[identity]

    async def ensure(self, profile):
        async with self.lock:
            if profile["id"] in self.apps:
                return self.apps[profile["id"]]
            app = self.application(profile)
            try:
                await self.stack.enter_async_context(app.router.lifespan_context(app))
            except BaseException:
                self.apps.pop(profile["id"], None)
                raise
            return app


def cookie(scope):
    values = SimpleCookie()
    values.load(dict(scope.get("headers", [])).get(b"cookie", b"").decode())
    return values["core_session"].value if "core_session" in values else ""


def create_unified_app(root, factory, web_root=None, origin="http://127.0.0.1:9310"):
    services = AccountServices(root, factory, web_root, origin)
    accounts = services.accounts

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            services.stack = stack
            for profile in accounts.profiles():
                if profile["active"]:
                    tenant = services.application(profile)
                    await stack.enter_async_context(tenant.router.lifespan_context(tenant))
            yield
            services.stack = None

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    # Preserve the owned initial application's diagnostic state for CLI/tests.
    app.state = services.initial.state
    app.state.accounts = accounts
    app.state.account_services = services

    @app.exception_handler(CoreError)
    async def error(request, exc):
        return JSONResponse(
            {"code": exc.code, "detail": str(exc)},
            status_code={
                "unauthorized": 401,
                "forbidden": 403,
                "not_found": 404,
                "revision_conflict": 409,
            }.get(exc.code, 422),
        )

    def current(request, *, admin=False):
        session = accounts.session(request.cookies.get("core_session", ""))
        if not session:
            raise CoreError("请先登录", "unauthorized")
        if admin and session["role"] != "admin":
            raise CoreError("需要管理员权限", "forbidden")
        if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(
            session["csrf"], request.headers.get("x-core-csrf", "")
        ):
            raise CoreError("请求验证失效，请刷新", "forbidden")
        return session

    @app.middleware("http")
    async def boundary(request, call_next):
        from .storage_admission import paused

        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and paused(root)
            and not request.url.path.startswith("/v1/storage/")
        ):
            return JSONResponse(
                {"code": "storage_migrating", "detail": "存储迁移中，请稍后重试"},
                503,
                headers={"Retry-After": "5"},
            )
        if request.headers.get("host") != origin.split("://", 1)[1]:
            return JSONResponse({"code": "invalid_host"}, 403)
        client_api = request.url.path in {
            "/v1/setup/authorizations",
            "/v1/identity/exchange",
            "/v1/identity/introspect",
            "/v1/identity/directory",
            "/v1/identity/revoke",
            "/v1/identity/task-account",
            "/v1/identity/bridge",
            "/v1/identity/peer-grant",
            "/v1/identity/peer-verify",
        } or (request.url.path.startswith("/v1/setup/authorizations/") and request.url.path.endswith("/exchange"))
        if (
            request.method not in {"GET", "HEAD"}
            and not client_api
            and request.headers.get("origin") != origin
        ):
            return JSONResponse({"code": "invalid_origin"}, 403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    async def cancel_account_tasks(identity, application_id=None):
        async with services.lock:
            tenant = services.apps.get(identity)
            runtime = getattr(tenant.state, "runtime", None) if tenant is not None else None
            if runtime is None:
                return
            if application_id is not None:
                from .bridges import Bridges

                allowed = {
                    Bridges.scope(bridge, application_id.removeprefix("dsh-"))
                    for bridge in accounts.bound_bridges(identity, application_id)
                }
            else:
                allowed = None
            jobs = [
                task["id"]
                for task in runtime.store.tasks(active=True)
                if allowed is None or task["request"]["application_id"] in allowed
            ]
        await asyncio.gather(
            *(runtime.invoke("task.cancel", {"id": identity}) for identity in jobs)
        )

    @app.post("/v1/session/local")
    async def local_session(request: Request):
        # Never switch an existing authenticated account implicitly.
        existing = accounts.session(request.cookies.get("core_session", ""))
        identity = existing["account_id"] if existing else accounts.initial["id"]
        if existing and not secrets.compare_digest(existing["csrf"], request.headers.get("x-core-csrf", "")):
            raise CoreError("请求验证失效，请刷新", "forbidden")
        token, csrf = accounts.issue(identity)
        response = JSONResponse({"csrf": csrf, "owner": accounts.account(identity)})
        response.set_cookie("core_session", token, httponly=True, samesite="strict", max_age=86400)
        response.set_cookie("core_csrf", csrf, samesite="strict", max_age=86400)
        return response

    @app.post("/v1/identity/login")
    async def login(request: Request, payload: Login):
        body = payload.model_dump()
        if not isinstance(body, dict):
            raise CoreError("登录信息无效")
        token, csrf = await asyncio.to_thread(
            accounts.login,
            body.get("username"),
            body.get("password"),
            bucket=(request.client.host if request.client else "local"),
        )
        response = JSONResponse(
            {"csrf": csrf, "owner": accounts.account(accounts.session(token)["account_id"])}
        )
        response.set_cookie("core_session", token, httponly=True, samesite="strict", max_age=86400)
        response.set_cookie("core_csrf", csrf, samesite="strict", max_age=86400)
        return response

    @app.post("/v1/identity/logout")
    async def logout(request: Request):
        value = current(request)
        accounts.revoke_all(value["account_id"])
        response = JSONResponse({"logged_out": True})
        response.delete_cookie("core_session")
        response.delete_cookie("core_csrf")
        return response

    @app.get("/v1/identity/me", response_model=IdentityMe)
    async def me(request: Request):
        value = current(request)
        from .identity_permissions import role_permissions

        profile = accounts.account(value["account_id"])
        return {"account": profile, "permissions": sorted(role_permissions(profile["role"]))}

    @app.get("/v1/identity/accounts", response_model=AccountList)
    async def profiles(request: Request):
        current(request, admin=True)
        return {"items": accounts.profiles()}

    @app.post("/v1/identity/accounts", response_model=AccountProfile)
    async def create(request: Request, payload: CreateAccount):
        current(request, admin=True)
        body = payload.model_dump()
        return accounts.create(
            body.get("username"),
            body.get("display_name"),
            body.get("password"),
            role=body.get("role", "user"),
        )

    @app.patch("/v1/identity/accounts/{identity}")
    async def update(identity: str, request: Request, payload: UpdateAccount):
        current(request, admin=True)
        body = payload.model_dump()
        if type(body.get("active")) is not bool or type(body.get("expected_revision")) is not int:
            raise CoreError("账号字段无效")
        result = accounts.update(
            identity, active=body["active"], expected_revision=body["expected_revision"]
        )
        if not body["active"]:
            await cancel_account_tasks(identity)
        return result

    @app.post("/v1/identity/invitations")
    async def invite(request: Request):
        current(request, admin=True)
        return {"invitation": accounts.invitation(), "expires_in": 86400}

    @app.post("/v1/identity/register", response_model=AccountProfile)
    async def register(request: Request, payload: RegisterAccount):
        body = payload.model_dump()
        if not isinstance(body.get("invitation"), str):
            raise CoreError("需要有效邀请", "forbidden")
        return accounts.create(
            body.get("username"),
            body.get("display_name"),
            body.get("password"),
            invite=body["invitation"],
        )

    @app.post("/v1/identity/applications", response_model=ApplicationRegistrationResult)
    async def register_application(request: Request, payload: Application):
        value = current(request, admin=True)
        body = payload.model_dump()
        client = accounts.register_client(body["id"], body["origin"], body["callback"])
        accounts.grant(value["account_id"], body["id"])
        if body.get("bridge_id"):
            tenant = await services.ensure(accounts.account(value["account_id"]))
            if not any(row["id"] == body["bridge_id"] for row in tenant.state.access.bridges()):
                raise CoreError("连接不存在", "not_found")
            accounts.bind_bridge(value["account_id"], body["id"], body["bridge_id"])
        return {**client, "account_id": value["account_id"]}

    @app.get("/v1/identity/authorizations")
    async def authorizations(request: Request):
        value = current(request)
        return {"items": accounts.applications(value["account_id"])}

    @app.delete("/v1/identity/authorizations/{application_id}")
    async def revoke_authorization(application_id: str, request: Request):
        value = current(request)
        accounts.revoke_application(value["account_id"], application_id)
        await cancel_account_tasks(value["account_id"], application_id)
        return {"revoked": True}

    @app.delete("/v1/identity/applications/{application_id}")
    async def remove_application(application_id: str, request: Request):
        current(request, admin=True)
        accounts.remove_client(application_id)
        await asyncio.gather(
            *(
                cancel_account_tasks(profile["id"], application_id)
                for profile in accounts.profiles()
            )
        )
        return {"revoked": True}

    @app.post("/v1/identity/accounts/{identity}/password")
    async def recover_password(identity: str, request: Request, payload: Password):
        current(request, admin=True)
        body = payload.model_dump()
        accounts.reset_password(identity, body.get("password"))
        return {"updated": True, "sessions_revoked": True}

    @app.get("/v1/identity/authorize")
    async def authorize(request: Request, application_id: str, state: str):
        if not 16 <= len(state) <= 128:
            raise CoreError("登录状态无效")
        value = current(request)
        code, client = accounts.code(value["account_id"], application_id)
        from urllib.parse import urlencode

        return RedirectResponse(
            client["origin"] + client["callback"] + "#" + urlencode({"code": code, "state": state}),
            303,
        )

    async def client_request(request):
        if "origin" in request.headers or "cookie" in request.headers:
            raise CoreError("需要应用服务请求", "forbidden")
        body = await request.json()
        if not isinstance(body, dict):
            raise CoreError("应用请求格式无效")
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        accounts.client(body.get("application_id"), token)
        return body, token

    from .storage_api import install as install_storage

    install_storage(app, Path(root), accounts, current)

    from .core_peer_api import install as install_peers

    install_peers(app, accounts, Path(root), client_request)

    @app.post("/v1/identity/exchange")
    async def exchange(request: Request):
        body, token = await client_request(request)
        return accounts.exchange(body["application_id"], token, body["code"])

    async def app_actor(request):
        body, _ = await client_request(request)
        session = accounts.session(body.get("session", ""), audience=body["application_id"])
        if not session:
            raise CoreError("应用登录已失效", "unauthorized")
        return body, session, accounts.account(session["account_id"])

    @app.post("/v1/identity/introspect")
    async def introspect(request: Request):
        _, session, profile = await app_actor(request)
        return {"account": profile, "session_id": session["digest"], "expires": session["expires"]}

    @app.post("/v1/identity/revoke")
    async def revoke(request: Request):
        body, _, _ = await app_actor(request)
        accounts.revoke(body["session"])
        return {"revoked": True}

    @app.post("/v1/identity/directory", response_model=ApplicationDirectoryResult)
    async def directory(request: Request, _query: ApplicationDirectory):
        body, _ = await client_request(request)
        if body.get("owner_user_id") is not None and body.get("session") is None:
            owner = body["owner_user_id"]
            if not accounts.has_grant(owner, body["application_id"]):
                raise CoreError("账号尚未授权此应用", "forbidden")
            profile = accounts.account(owner)
        else:
            body, _, profile = await app_actor(request)
        ids, names = body.get("ids", []), body.get("usernames", [])
        if not isinstance(ids, list) or not isinstance(names, list) or len(ids) + len(names) > 4000:
            raise CoreError("账号查询范围无效")

        def public(p):
            return {
                "id": p["id"],
                "username": p["username"],
                "display_name": p["display_name"],
                "active": bool(p["active"]),
            }

        return {
            "caller": {
                **public(profile),
                "platform_role": profile["role"],
                "revision": profile["revision"],
            },
            "profiles": [
                public(p) for p in accounts.profiles() if p["id"] in ids or p["username"] in names
            ],
        }

    @app.post("/v1/identity/task-account")
    async def task_account(request: Request):
        body, _ = await client_request(request)
        owner = body.get("owner_user_id")
        if not accounts.has_grant(owner, body["application_id"]):
            raise CoreError("账号尚未授权此应用", "forbidden")
        profile = accounts.account(owner)
        from .identity_permissions import role_permissions

        return {
            "account": profile,
            "allowed": body.get("permission") in role_permissions(profile["role"]),
        }

    @app.post("/v1/identity/bridge")
    async def app_bridge(request: Request):
        body, session, profile = await app_actor(request)
        tenant = await services.ensure(profile)
        pair = tenant.state.access.pair(body["application_id"])
        accounts.bind_bridge(profile["id"], body["application_id"], pair["id"])
        return {**pair, "account_id": profile["id"]}

    from .setup_authorizations import register_setup_routes

    register_setup_routes(app, root, services, current, origin)

    @app.get("/v1/openapi.json")
    async def openapi():
        return combined_schema()

    def combined_schema():
        from fastapi.openapi.utils import get_openapi

        identity = get_openapi(title="未来道具研究所", version="0.4.0", routes=app.routes)
        core = services.initial.openapi()
        core["info"] = {"title": "未来道具研究所", "version": "0.4.0"}
        core["paths"].update(
            {
                path: value
                for path, value in identity["paths"].items()
                if path == "/v1/session/local" or path.startswith(("/v1/identity/", "/v1/storage", "/v1/setup/"))
            }
        )
        core.setdefault("components", {}).setdefault("schemas", {}).update(
            identity.get("components", {}).get("schemas", {})
        )
        return core

    app.openapi = combined_schema

    @app.get("/login")
    async def login_page(request: Request):
        session = accounts.session(request.cookies.get("core_session", ""))
        if session and request.query_params.get("application_id"):
            from urllib.parse import urlencode

            return RedirectResponse(
                "/v1/identity/authorize?" + urlencode(dict(request.query_params)), 303
            )
        if not session and request.query_params.get("application_id"):
            from urllib.parse import urlencode
            from .identity_login import local_authorization_html

            return HTMLResponse(local_authorization_html(
                "/v1/identity/authorize?" + urlencode(dict(request.query_params))
            ))
        if request.query_params.get("core") != "1":
            return RedirectResponse("/", 303)
        return HTMLResponse(LOGIN_HTML.replace('<p>登录后使用你的应用与私人资源。</p>', '<p>日常使用无需密码。<a href="/">以本机所有者进入</a>；此表单用于已有 Core 账号。</p>'))

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        include_in_schema=False,
    )
    async def dispatch(path: str, request: Request):
        value = accounts.session(request.cookies.get("core_session", ""))
        profile = accounts.account(value["account_id"]) if value else accounts.initial
        if profile["role"] != "admin":
            forbidden = path.startswith(
                ("v1/code-agents", "v1/local-models")
            ) and request.method not in {"GET", "HEAD"}
            if request.method == "POST" and path in {"v1/rpc", "v1/tasks"}:
                payload = await request.json()
                params = payload.get("params", {}) if path == "v1/rpc" else payload
                forbidden = (
                    forbidden
                    or payload.get("method") in {"agent.detect", "local.manage"}
                    or params.get("kind") == "agent"
                )
            if forbidden:
                raise CoreError("此操作需要本机管理员权限", "forbidden")
        tenant = await services.ensure(profile)
        from starlette.responses import Response

        class Forward(Response):
            async def __call__(self, scope, receive, send):
                await tenant(scope, receive, send)

        return Forward()

    @app.websocket("/{path:path}")
    async def ws(socket: WebSocket, path: str):
        if path == "v1/model-test/realtime":
            session = accounts.session(cookie(socket.scope))
            if session:
                tenant = await services.ensure(accounts.account(session["account_id"]))
                return await tenant(socket.scope, socket.receive, socket.send)
            await socket.close(code=1008)
            return
        token = (
            dict(socket.scope.get("headers", []))
            .get(b"authorization", b"")
            .decode()
            .removeprefix("Bearer ")
            or parse_qs(socket.scope.get("query_string", b"").decode()).get("token", [""])[0]
        )
        for profile in accounts.profiles():
            if not profile["active"]:
                continue
            tenant = await services.ensure(profile)
            try:
                tenant.state.access.authenticate_bridge(token)
            except CoreError:
                continue
            return await tenant(socket.scope, socket.receive, socket.send)
        await socket.close(code=1008)

    return app


LOGIN_HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>登录 · 未来道具研究所</title><style>body{font:16px system-ui;background:#1c1e1b;color:#eee7da;display:grid;place-items:center;min-height:95vh}form{width:min(360px,85vw);padding:32px;border:1px solid #393e37;border-radius:20px}label{display:grid;gap:8px;margin:20px 0}input,button{font:inherit;padding:12px;border-radius:10px;border:1px solid #555;background:#282c26;color:inherit}button{background:#eb8054;cursor:pointer;width:100%}p{line-height:1.7}#error{color:#ff9e87}</style><form><h1>未来道具研究所</h1><p>登录后使用你的应用与私人资源。</p><label>账号<input name="username" autocomplete="username" required maxlength="64"></label><label>密码<input name="password" type="password" autocomplete="current-password" required minlength="10" maxlength="256"></label><button>登录</button><p id="error" role="alert"></p></form><script>const form=document.querySelector('form');form.onsubmit=async e=>{e.preventDefault();const button=form.querySelector('button');button.disabled=true;try{const response=await fetch('/v1/identity/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(form)))});if(!response.ok)throw new Error('账号或密码无效，或登录尝试过多。');const query=new URLSearchParams(location.search);const app=query.get('application_id'),state=query.get('state');location.assign(app&&state?'/v1/identity/authorize?'+new URLSearchParams({application_id:app,state}):'/')}catch(error){document.querySelector('#error').textContent=error.message;button.disabled=false}};</script></html>"""
