"""Public application-scoped article synchronization using Core-owned credentials."""

import hashlib
import json
import time
from dataclasses import asdict, replace
from uuid import NAMESPACE_URL, UUID, uuid5

from dsh_core_worker.contracts import CoreError

from .contracts.external_documents import ExternalError
from .contracts.external_sync import ExternalClose, ExternalRead, ExternalStep, ExternalStepEvidence
from .contracts.integration_intents import IntegrationScope
from .external_reads import ExternalReads
from .external_steps import ExternalSteps
from .identity_actor import Actor


class ExternalSync:
    def __init__(self, runtime, integrations, access):
        self.runtime, self.integrations, self.access = runtime, integrations, access
        self.reads = ExternalReads(
            integrations.database,
            cipher=integrations.cipher,
            transport=integrations.feishu_transport,
        )
        self.steps = ExternalSteps(
            integrations.database,
            cipher=integrations.cipher,
            transport=integrations.feishu_transport,
        )

    def active(self, actor, application):
        if not hasattr(self.access, "accounts"):
            return False
        profile = self.access.profile()
        if actor.user_id != profile["id"] or actor.platform_role != profile["role"]:
            return False
        if not self.access.accounts.has_grant(actor.user_id, "dsh-" + application):
            return False
        if actor.auth_kind == "session":
            with self.access.accounts.connection() as db:
                return (
                    db.execute(
                        "SELECT 1 FROM account_session WHERE digest=? AND account_id=? AND audience=? AND expires>?",
                        (actor.session_id, actor.user_id, "dsh-" + application, int(time.time())),
                    ).fetchone()
                    is not None
                )
        return actor.api_key_id == "dsh-plugin-v1"

    def intent(self, identity, application):
        value = self.runtime.store.config("article-intent:" + str(identity), None)["value"]
        if not value or value["application"] != application:
            raise CoreError("同步授权不存在", "not_found")
        actor = Actor(**{**value["actor"], "scopes": tuple(value["actor"]["scopes"])})
        return value, actor, IntegrationScope.model_validate(value["scope"])

    async def invoke(self, method, params, application):
        if application != "thought-workshop":
            raise CoreError("此应用未开放文章同步", "forbidden")
        try:
            return await self.dispatch(method, params, application)
        except ExternalError as error:
            raise CoreError(str(error), error.code, status=error.status) from None

    async def dispatch(self, method, params, application):
        if method == "external.connections":
            actor = Actor(**{**params["actor"], "scopes": tuple(params["actor"]["scopes"])})
            if not self.active(actor, application):
                raise CoreError("应用授权已撤销", "forbidden")
            async with self.integrations.database.sessions() as session:
                return (await self.integrations.external_catalog(session).list()).model_dump(
                    mode="json"
                )
        if method == "external.enroll":
            scope = IntegrationScope.model_validate(params["scope"])
            actor = Actor(**{**params["actor"], "scopes": tuple(params["actor"]["scopes"])})
            if (
                scope.kind != "external.sync"
                or params["source_path"]
                != f"/v1/thought-workshop/notes/{scope.source_resource_id}/external-sync/tasks"
                or not self.active(actor, application)
            ):
                raise CoreError("同步授权范围无效", "forbidden")
            identity = str(uuid5(NAMESPACE_URL, application + ":" + str(params["request_id"])))
            value = {
                "application": application,
                "actor": asdict(actor),
                "scope": scope.model_dump(mode="json"),
                "source_path": params["source_path"],
            }
            fingerprint = hashlib.sha256(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            saved = self.runtime.store.config("article-intent:" + identity, None)
            if saved["value"] and saved["value"] != {**value, "fingerprint": fingerprint}:
                raise CoreError("同步请求内容不一致", "idempotency_conflict")
            if not saved["value"]:
                self.runtime.store.save_config(
                    "article-intent:" + identity,
                    {**value, "fingerprint": fingerprint},
                    saved["revision"],
                )
            return {"id": identity, "fingerprint": fingerprint, "scope": value["scope"]}
        if method == "external.intent":
            value, actor, scope = self.intent(params["id"], application)
            return {
                "allowed": self.active(actor, application),
                "expires_at": int(time.time()) + 5,
                "scope": value["scope"],
                "actor": value["actor"],
            }
        if method == "external.read":
            actor = Actor(**{**params["actor"], "scopes": tuple(params["actor"]["scopes"])})
            if not self.active(actor, application):
                raise CoreError("同步授权已撤销", "forbidden")
            body = ExternalRead.model_validate(params["body"])

            async def check():
                if not self.active(actor, application):
                    raise ExternalError("同步授权已撤销", 403, "authorization_revoked")

            return await self.reads.execute("local-owner", params["connection_id"], body, check)
        if method == "external.close":
            actor = Actor(**{**params["actor"], "scopes": tuple(params["actor"]["scopes"])})
            if not self.active(actor, application):
                raise CoreError("同步授权已撤销", "forbidden")
            return await self.steps.close(
                "local-owner", ExternalClose.model_validate(params["body"])
            )
        if method in {"external.progress", "external.step"}:
            value, actor, scope = self.intent(params["id"], application)

            async def check():
                if not self.active(actor, application):
                    raise ExternalError("同步授权已撤销", 403, "authorization_revoked")

            await check()
            local_actor = replace(actor, user_id="local-owner")
            if method == "external.step":
                return await self.steps.execute(
                    local_actor,
                    scope,
                    UUID(params["id"]),
                    ExternalStep.model_validate(params["body"]),
                    check,
                )
            if params["body"].get("op") == "checkpoint":
                return await self.steps.progress(local_actor, scope, UUID(params["id"]))
            if "step_id" in params["body"]:
                return await self.steps.step_proof(
                    local_actor,
                    scope,
                    UUID(params["id"]),
                    ExternalStepEvidence.model_validate(params["body"]),
                )
            return await self.reads.execute(
                "local-owner",
                scope.connection_id,
                ExternalRead.model_validate(params["body"]),
                check,
                scope=scope,
            )
        raise CoreError("同步操作未开放", "forbidden")
