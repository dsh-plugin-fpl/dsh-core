"""An encrypted, account-owned remote cookie jar. Passwords are never persisted."""

import asyncio
import json
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

MAX_RESPONSE = 64 * 1024 * 1024
DEFAULT_SITE = "http://127.0.0.1:3000"


def site_url(value):
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        raise HTTPException(422, "网站地址无效") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise HTTPException(422, "请输入网站根地址")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(422, "非本机网站须使用 HTTPS")
    try:
        _ = parsed.port
    except ValueError:
        raise HTTPException(422, "网站端口无效") from None
    return value.strip().rstrip("/")


class Website:
    def __init__(self, access):
        self.access = access
        self.lock = asyncio.Lock()
        self.transport = None  # Explicit deterministic test transport, never configured by HTTP.

    def load(self):
        value = self.access.read("fpl-website.enc", {"schema_version": 1, "site": DEFAULT_SITE})
        if value.get("schema_version") != 1:
            raise HTTPException(503, "网站连接配置版本不兼容")
        return value

    def save(self, state):
        self.access.write("fpl-website.enc", {**state, "schema_version": 1})

    def summary(self, state):
        return {
            "site": state["site"],
            "account": state.get("account"),
            "permissions": state.get("permissions", []),
            "must_change_password": state.get("must_change_password", False),
        }

    async def send(self, state, method, path, *, authenticated=True, **kwargs):
        if authenticated and not state.get("cookies"):
            raise HTTPException(
                424,
                {"code": "website_login_required", "message": "请先登录 FPL 网站"},
                headers={"X-FPL-Auth-Required": "1"},
            )
        cookies = httpx.Cookies()
        for cookie in state.get("cookies", []):
            cookies.set(
                cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie["path"]
            )
        headers = {"Origin": state["site"], "Referer": state["site"] + "/"}
        csrf = next((c["value"] for c in state.get("cookies", []) if c["name"] == "fpl_csrf"), "")
        if csrf:
            headers["X-CSRF-Token"] = csrf
        try:
            async with (
                httpx.AsyncClient(
                    base_url=state["site"],
                    cookies=cookies,
                    headers=headers,
                    timeout=30,
                    follow_redirects=False,
                    transport=self.transport,
                    trust_env=False,
                ) as client,
                client.stream(method, path, **kwargs) as response,
            ):
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE:
                        raise HTTPException(413, "网站返回内容超过 64 MiB 限制")
                if response.status_code >= 300:
                    if authenticated and response.status_code == 401:
                        state.pop("cookies", None)
                        state.pop("account", None)
                        state.pop("permissions", None)
                        self.save(state)
                    try:
                        detail = json.loads(data).get("detail", {})
                        message = detail if isinstance(detail, str) else detail.get("message", "")
                    except (ValueError, AttributeError):
                        message = ""
                    defaults = {
                        401: "网站账号或密码错误，或登录已失效",
                        403: "网站账号没有此操作权限",
                        404: "网站资源不存在或版本已撤回；请确认原站已升级",
                        409: "作品已更新，请重新读取后操作",
                        429: "网站登录尝试过多，请稍后重试",
                    }
                    raise HTTPException(
                        424 if response.status_code == 401 else min(response.status_code, 599),
                        {
                            "code": "website_request_failed",
                            "message": message
                            or defaults.get(response.status_code, "网站请求失败"),
                        },
                        headers={"X-FPL-Auth-Required": "1"}
                        if response.status_code == 401
                        else None,
                    )
                state["cookies"] = [
                    {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                    for c in client.cookies.jar
                ]
                self.save(state)
                return bytes(data), response.headers.get("content-type", "application/json")
        except httpx.HTTPError:
            raise HTTPException(
                502, "FPL 网站暂不可达，请检查地址与服务；本机功能仍可使用"
            ) from None

    async def status(self):
        async with self.lock:
            state = self.load()
            if state.get("cookies"):
                try:
                    data, _ = await self.send(state, "GET", "/v1/auth/me")
                    self.identify(state, json.loads(data))
                    self.save(state)
                except HTTPException as error:
                    if error.status_code != 424:
                        return {**self.summary(state), "offline": True}
            return self.summary(state)

    @staticmethod
    def identify(state, body):
        user = body["user"]
        state.update(
            account={key: user.get(key) for key in ("id", "username", "display_name")},
            permissions=body.get("permission_keys", []),
            must_change_password=body.get("must_change_password", False),
        )

    async def login(self, site, username, password):
        async with self.lock:
            state = {"schema_version": 1, "site": site_url(site)}
            data, _ = await self.send(
                state,
                "POST",
                "/v1/auth/login",
                authenticated=False,
                json={"username": username, "password": password},
            )
            self.identify(state, json.loads(data))
            self.save(state)
            return self.summary(state)

    async def logout(self):
        async with self.lock:
            state = self.load()
            try:
                if state.get("cookies"):
                    await self.send(state, "POST", "/v1/auth/logout")
            except HTTPException:
                # Local disconnect must succeed even when remote revocation is unavailable.
                pass
            finally:
                self.save({"schema_version": 1, "site": state["site"]})
            return {"disconnected": True}

    async def change_password(self, body):
        async with self.lock:
            state = self.load()
            await self.send(state, "POST", "/v1/auth/change-password", json=body)
            raw, _ = await self.send(state, "GET", "/v1/auth/me")
            self.identify(state, json.loads(raw))
            self.save(state)
            return self.summary(state)
