"""Browser-approved, proof-bound installation grants; secrets are delivered only to the Host."""

import asyncio
import hashlib
import secrets
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from .access import Access
from .contracts import CoreError

APPLICATIONS = {
    "dsh-visual-workshop": "visual-workshop",
    "dsh-video-editor": "video-editor",
    "dsh-presentation-workshop": "presentation-workshop",
    "dsh-thought-workshop": "thought-workshop",
    "dsh-galgame-workshop": "galgame-workshop",
    "dsh-audio-annotation": "audio-annotation",
}


class SetupApplication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Literal[tuple(APPLICATIONS)]
    origin: str = Field(max_length=100)
    expected_account: str | None = Field(default=None, max_length=100)


class SetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str = Field(min_length=1, max_length=120)
    challenge: str = Field(pattern=r"^[a-f0-9]{64}$")
    applications: list[SetupApplication] = Field(default_factory=list, max_length=6)
    expected_account: str | None = Field(default=None, max_length=100)


class SetupProof(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verifier: str = Field(min_length=40, max_length=128, repr=False)


def register_setup_routes(app, root, services, current, origin):
    access = Access(Path(root) / "setup")
    lock = asyncio.Lock()

    def name(identity):
        if len(identity) != 36 or any(c not in "0123456789abcdef-" for c in identity):
            raise CoreError("安装授权不存在", "not_found")
        return "request-" + identity + ".enc"

    def read(identity):
        value = access.read(name(identity))
        if not value or value["expires"] < time.time():
            raise CoreError("安装授权已过期，请重新发起", "setup_expired")
        return value

    def summary(value):
        return {
            "id": value["id"], "profile": value["profile"],
            "applications": value["applications"], "expires": value["expires"],
            "status": "approved" if value.get("result") else "pending",
        }

    @app.post("/v1/setup/authorizations")
    async def create(payload: SetupRequest):
        from urllib.parse import urlsplit

        applications = [row.model_dump() for row in payload.applications]
        if len({row["id"] for row in applications}) != len(applications):
            raise CoreError("安装应用重复", "validation")
        for row in applications:
            url = urlsplit(row["origin"])
            if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port
                or not 1024 <= url.port <= 65535
                or row["origin"] != f"http://127.0.0.1:{url.port}"):
                raise CoreError("安装授权仅支持明确的本机服务", "validation")
        async with lock:
            live = 0
            for path in access.root.glob("request-*.enc"):
                value = access.read(path.name)
                if value.get("expires", 0) < time.time():
                    path.unlink(missing_ok=True)
                else:
                    live += 1
            if live >= 32:
                raise CoreError("安装授权请求过多，请稍后重试", "setup_busy")
            identity = str(uuid4())
            value = {"id": identity, "profile": payload.profile, "challenge": payload.challenge,
                     "applications": applications, "expected_account": payload.expected_account, "expires": time.time() + 300}
            access.write(name(identity), value)
        return {"id": identity, "url": origin + "/setup/authorize?request_id=" + identity,
                "expires": value["expires"]}

    @app.get("/v1/setup/authorizations/{identity}")
    async def describe(identity: str, request: Request):
        current(request)
        return summary(read(identity))

    @app.post("/v1/setup/authorizations/{identity}/approve")
    async def approve(identity: str, request: Request):
        session = current(request)
        profile = services.accounts.account(session["account_id"])
        async with lock:
            value = read(identity)
            if value.get("expected_account") and value["expected_account"] != profile["id"]:
                raise CoreError("当前安装已绑定其他账号，不会更换连接或转移私人数据", "forbidden")
            if value.get("account_id") and value["account_id"] != profile["id"]:
                raise CoreError("安装请求已绑定另一个账号", "forbidden")
            if value.get("result"):
                if value["result"]["account_id"] != profile["id"]:
                    raise CoreError("安装授权已绑定另一个账号", "forbidden")
                return summary(value)
            # Validate every application before issuing any grant.
            for row in value["applications"]:
                if row.get("expected_account") and row["expected_account"] != profile["id"]:
                    raise CoreError("现有安装属于另一个账号，不会转移私人数据", "forbidden")
                try:
                    client = services.accounts.client(row["id"])
                    if client["origin"] != row["origin"]:
                        raise CoreError("应用已绑定其他地址，请先显式恢复配对", "revision_conflict")
                except CoreError as error:
                    if error.code != "unauthorized":
                        raise
                    if profile["role"] != "admin":
                        raise CoreError("首次登记工坊需要管理员授权", "forbidden") from None
            tenant = await services.ensure(profile)
            value["account_id"] = profile["id"]
            access.write(name(identity), value)
            result = {"account_id": profile["id"], "applications": {}}
            # Persist each issued result: retries never create another bridge for the same step.
            progress = value.get("progress", {})
            for row in value["applications"]:
                if row["id"] not in progress:
                    try:
                        client = services.accounts.client(row["id"])
                        created = False
                    except CoreError:
                        client = {"id": row["id"], "token": secrets.token_urlsafe(40), "origin": row["origin"], "callback": "/apps/" + APPLICATIONS[row["id"]]}
                        created = True
                    bridge = {"id": secrets.token_hex(8), "name": row["id"], "token": secrets.token_urlsafe(40)}
                    progress[row["id"]] = {"version": 1, "origin": origin, "account_id": profile["id"],
                                           "client": client if created else None, "bridge": bridge,
                                           "application_origin": row["origin"]}
                    value["progress"] = progress
                    access.write(name(identity), value)
                step = progress[row["id"]]
                if step["client"]:
                    c = step["client"]
                    services.accounts.register_client(c["id"], c["origin"], c["callback"], token=c["token"])
                services.accounts.grant(profile["id"], row["id"])
                tenant.state.access.ensure_pair(step["bridge"])
                services.accounts.bind_bridge(profile["id"], row["id"], step["bridge"]["id"])
            result["applications"] = progress
            if "bridge_intent" not in value:
                value["bridge_intent"] = {"id": secrets.token_hex(8), "name": "DSH · " + value["profile"], "token": secrets.token_urlsafe(40)}
                access.write(name(identity), value)
            result["bridge"] = tenant.state.access.ensure_pair(value["bridge_intent"])
            value["result"] = result
            access.write(name(identity), value)
            return summary(value)

    @app.post("/v1/setup/authorizations/{identity}/exchange")
    async def exchange(identity: str, payload: SetupProof):
        async with lock:
            value = read(identity)
            challenge = hashlib.sha256(payload.verifier.encode()).hexdigest()
            if not secrets.compare_digest(challenge, value["challenge"]):
                raise CoreError("安装领取证明无效", "unauthorized")
            if not value.get("result"):
                return {"status": "pending"}
            result = value["result"]
            profile = services.accounts.account(result["account_id"])
            tenant = await services.ensure(profile)
            tenant.state.access.authenticate_bridge(result["bridge"]["token"])
            for application in result["applications"]:
                if not services.accounts.has_grant(result["account_id"], application):
                    raise CoreError("安装授权已撤销", "forbidden")
                tenant.state.access.authenticate_bridge(result["applications"][application]["bridge"]["token"])
            # Same-proof retries return the same encrypted receipt until expiry.
            return {"status": "approved", **result}
