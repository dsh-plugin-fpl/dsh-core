"""Exact application-to-application capabilities issued by the common identity owner."""

import json
import secrets
import time
from dataclasses import asdict

from fastapi import APIRouter, Request

from .access import Access
from .contracts import CoreError
from .integrations.identity_actor import Actor

SERVICES = {
    "visual": "dsh-visual-workshop",
    "video": "dsh-video-editor",
    "galgame": "dsh-galgame-workshop",
    "annotation": "dsh-audio-annotation",
    "thought": "dsh-thought-workshop",
    "presentation": "dsh-presentation-workshop",
}


def install(app, accounts, root, client_request):
    router = APIRouter(prefix="/v1/identity")
    cipher = Access(root / "identity/peers").cipher

    def active(source, actor):
        profile = accounts.account(actor.user_id)
        if actor.platform_role != profile["role"] or not accounts.has_grant(actor.user_id, source):
            raise CoreError("应用账号授权已失效", "forbidden")
        if actor.auth_kind == "session":
            with accounts.connection() as db:
                found = db.execute(
                    "SELECT 1 FROM account_session WHERE digest=? AND account_id=? AND audience=? AND expires>?",
                    (actor.session_id, actor.user_id, source, int(time.time())),
                ).fetchone()
            if not found:
                raise CoreError("应用会话已失效", "unauthorized")
        elif actor.api_key_id != "dsh-plugin-v1":
            raise CoreError("需要受限应用身份", "forbidden")
        return profile

    @router.post("/peer-grant")
    async def grant(request: Request):
        body, _ = await client_request(request)
        source = body["application_id"]
        target = SERVICES.get(body.get("target_service"))
        if source not in SERVICES.values() or not target or target == source:
            raise CoreError("应用交接范围无效", "forbidden")
        actor = Actor(**{**body["actor"], "scopes": tuple(body["actor"]["scopes"])})
        authority_source = source
        depth = 0
        if body.get("parent_grant"):
            try:
                parent = json.loads(cipher.decrypt(body["parent_grant"].encode(), ttl=300))
            except Exception:
                raise CoreError("上游交接授权已失效", "unauthorized") from None
            if (
                parent["target"] != source
                or parent["actor"]["user_id"] != actor.user_id
                or parent.get("depth", 0) >= 4
            ):
                raise CoreError("交接授权链无效", "forbidden")
            authority_source = parent.get("authority_source", parent["source"])
            depth = parent.get("depth", 0) + 1
            actor = Actor(**{**parent["actor"], "scopes": tuple(parent["actor"]["scopes"])})
        active(authority_source, actor)
        raw_headers = body.get("headers", {})
        if (
            not isinstance(raw_headers, dict)
            or len(json.dumps(raw_headers)) > 512 * 1024
            or any(
                not isinstance(k, str)
                or not isinstance(v, str)
                or "\r" in v
                or "\n" in v
                or not (
                    k.startswith("x-fpl-")
                    or k in {"range", "if-none-match", "if-modified-since", "accept"}
                )
                for k, v in raw_headers.items()
            )
        ):
            raise CoreError("交接元数据无效")
        if not accounts.has_grant(actor.user_id, target):
            raise CoreError("请先用同一 Core 账号登录目标应用", "forbidden")
        client = accounts.client(target)
        method = body.get("method")
        path = body.get("path", "")
        checksum = body.get("body_sha256", "")
        headers = body.get("assertions", [])
        if (
            method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
            or not path.startswith(("/v1/", "/_internal/v1/"))
            or len(path) > 8192
            or len(checksum) != 64
            or any(c not in "abcdef0123456789" for c in checksum)
            or len(headers) > 8
        ):
            raise CoreError("交接请求无效")
        value = {
            "source": source,
            "target": target,
            "target_service": body["target_service"],
            "actor": asdict(actor),
            "method": method,
            "path": path,
            "body_sha256": checksum,
            "assertions": headers,
            "headers": raw_headers,
            "authority_source": authority_source,
            "depth": depth,
            "expires": int(time.time()) + 300,
            "id": secrets.token_hex(16),
        }
        token = cipher.encrypt(json.dumps(value).encode()).decode()
        return {"endpoint": client["origin"] + "/__core_peer", "token": token}

    @router.post("/peer-verify")
    async def verify(request: Request):
        body, _ = await client_request(request)
        try:
            value = json.loads(cipher.decrypt(body["grant"].encode(), ttl=300))
        except Exception:
            raise CoreError("交接授权已失效", "unauthorized") from None
        if value["target"] != body["application_id"]:
            raise CoreError("交接目标不一致", "forbidden")
        actor = Actor(**{**value["actor"], "scopes": tuple(value["actor"]["scopes"])})
        profile = active(value.get("authority_source", value["source"]), actor)
        accounts.client(value["target"])
        if not accounts.has_grant(actor.user_id, value["target"]):
            raise CoreError("目标应用授权已撤销", "forbidden")
        return {**value, "account": profile}

    app.include_router(router)
