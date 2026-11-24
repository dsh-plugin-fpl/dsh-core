"""Retirement preserves historical receipts and never resumes a removed adapter."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from dsh_core_worker.contracts import Connection, CoreError, Execution, PROVIDERS
from dsh_core_worker.extracted.apps.model_service.providers.common import sanitize_provider_message
from dsh_core_worker.access import Access
from dsh_core_worker.accounts import Accounts
from dsh_core_worker.http_schemas import TaskOut
from dsh_core_worker.retired_providers import RETIRED_PROVIDER_KINDS, retire_providers
from dsh_core_worker.runtime import Runtime
from dsh_core_worker.service import create_app
from dsh_core_worker.store import Store
from dsh_core_worker.task_views import TaskViews
from dsh_core_worker.storage_model_import import import_catalog


def seed(store):
    providers = [
        {"id": "old", "kind": "senseaudio_video", "models": [{"id": "old-model"}]},
        {"id": "ark", "kind": "volcengine_video", "models": [{"id": "ark-model"}]},
    ]
    store.save_config(
        "catalog",
        {
            "providers": providers,
            "suppliers": [{"id": "shared-group", "name": "Shared"}],
            "default_model_ids": {"image_generation": "old-model", "video_generation": "ark-model"},
        },
        0,
    )
    store.save_config(
        "connections",
        [
            {"id": "old", "kind": "senseaudio_video", "credential_ref": "PRIVATE_OLD"},
            {"id": "ark", "kind": "volcengine_video", "credential_ref": "PROVIDER_ark"},
        ],
        0,
    )
    store.save_config(
        "defaults",
        {
            "image_generation": {"connection_id": "old", "model": "same-slug"},
            "video_generation": {"connection_id": "ark", "model": "same-slug"},
        },
        0,
    )
    for ref in ("PRIVATE_OLD", "PROVIDER_old", "PROVIDER_ark"):
        store.save_config("credential:" + ref, "isolated-ciphertext", 0)
    tasks = []
    for status in ("queued", "processing", "recoverable", "completed", "failed", "cancelled"):
        request = Execution(
            request_id=status,
            application_id="core",
            connection_id="old",
            model="same-slug",
            capability="video_generation",
        )
        task, _ = store.admit(request, {"id": "old", "kind": "senseaudio_video", "name": "Legacy"})
        task = store.update(
            task["id"],
            status=status,
            provider_task_id="accepted-upstream",
            result={"resources": []},
            text="kept partial output",
        )
        tasks.append(task)
    return tasks


def test_retirement_is_atomic_idempotent_and_keeps_history_and_other_providers(tmp_path):
    store = Store(tmp_path)
    try:
        tasks = seed(store)
        store.db.execute(
            "CREATE TRIGGER fail_cleanup BEFORE DELETE ON configs "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
        with pytest.raises(Exception, match="injected failure"):
            retire_providers(store)
        assert len(store.config("catalog")["value"]["providers"]) == 2
        assert store.task(tasks[0]["id"])["status"] == "queued"
        assert not any(
            e["value"].get("status") == "interrupted" for e in store.events(tasks[0]["id"], after=0)
        )
        store.db.execute("DROP TRIGGER fail_cleanup")
        assert retire_providers(store) == 1
        catalog = store.config("catalog")
        assert catalog["revision"] == 2
        assert [p["id"] for p in catalog["value"]["providers"]] == ["ark"]
        assert catalog["value"]["suppliers"] == [{"id": "shared-group", "name": "Shared"}]
        assert catalog["value"]["default_model_ids"] == {"video_generation": "ark-model"}
        assert set(store.config("defaults")["value"]) == {"video_generation"}
        assert store.config("credential:PRIVATE_OLD")["revision"] == 0
        assert store.config("credential:PROVIDER_old")["revision"] == 0
        assert store.config("credential:PROVIDER_ark")["value"] == "isolated-ciphertext"
        for before in tasks:
            after = store.task(before["id"])
            assert after["provider_task_id"] == "accepted-upstream"
            assert after["text"] == before["text"] and after["result"] == before["result"]
            assert after["request"] == before["request"]
            assert TaskOut.model_validate(after).can_resume is False
            if before["status"] in {"queued", "processing", "recoverable"}:
                assert after["status"] == "interrupted"
                assert after["error"]["code"] == "provider_removed"
            else:
                assert after == before
        assert retire_providers(store) == 0
        assert store.config("catalog") == catalog
    finally:
        store.db.close()


def test_shared_secret_and_independent_account_are_preserved(tmp_path):
    for account in ("one", "two"):
        store = Store(tmp_path / account)
        seed(store)
        connections = store.config("connections")
        connections["value"][1]["credential_ref"] = "PRIVATE_OLD"
        store.save_config("connections", connections["value"], connections["revision"])
        if account == "one":
            retire_providers(store)
            assert store.config("credential:PRIVATE_OLD")["value"] == "isolated-ciphertext"
        else:
            assert len(store.config("catalog")["value"]["providers"]) == 2
        store.db.close()


@pytest.mark.asyncio
async def test_runtime_retires_before_scheduling_and_rejects_manual_resume(tmp_path):
    store = Store(tmp_path)
    tasks = seed(store)
    store.db.close()

    async def unexpected(*args, **kwargs):
        raise AssertionError("removed provider must never execute")

    runtime = Runtime(tmp_path, unexpected, unexpected)
    try:
        assert runtime.running == {}
        for task in tasks:
            with pytest.raises(CoreError) as error:
                await runtime.invoke("task.resume", {"id": task["id"]})
            assert error.value.code == "provider_removed"
        view = TaskViews(
            SimpleNamespace(
                state=SimpleNamespace(
                    runtime=runtime, catalog=SimpleNamespace(view=lambda: {"providers": []})
                )
            ),
            None,
        )
        assert (
            view.projection(runtime.store.task(tasks[0]["id"]))["can_retry_materialization"]
            is False
        )
    finally:
        await runtime.close()


@pytest.mark.parametrize("kind", sorted(RETIRED_PROVIDER_KINDS))
def test_removed_kinds_are_not_public_or_accepted(kind):
    assert kind not in PROVIDERS
    with pytest.raises(ValueError):
        Connection(id="removed", name="Removed", kind=kind, base_url="https://example.com")


def test_realtime_compatibility_endpoints_have_no_provider(tmp_path):
    origin = "http://testserver"
    app = create_app(tmp_path / "service", origin=origin)
    with TestClient(app) as client:
        session = client.post(
            "/v1/session", json={"ticket": app.state.access.ticket()}, headers={"Origin": origin}
        )
        assert session.status_code == 200
        assert client.get("/v1/model-test/realtime-options").json() == {"models": []}
        with client.websocket_connect(
            "/v1/model-test/realtime", headers={"Origin": origin}
        ) as socket:
            assert socket.receive_json()["code"] == "capability_unavailable"
        contract = client.get("/v1/openapi.json").json()
        assert "senseaudio" not in json.dumps(contract).lower()


def test_shared_message_sanitizer_still_redacts_secrets():
    result = sanitize_provider_message("Bearer hidden token=private data:image/png;base64,AAAA")
    assert all(secret not in result for secret in ("hidden", "private", "AAAA"))
    assert result.count("[REDACTED]") == 3


def test_all_active_accounts_retire_on_service_startup(tmp_path):
    root = tmp_path / "service"
    accounts = Accounts(root / "identity")
    other = accounts.create("second", "Second", "isolated-password-123")
    for profile in (accounts.initial, other):
        tenant = root if profile["legacy"] else root / "accounts" / profile["id"]
        store = Store(tenant / "state")
        seed(store)
        store.close()
    app = create_app(root, origin="http://testserver")
    with TestClient(app) as client:
        for profile in (accounts.initial, other):
            token, _ = accounts.issue(profile["id"])
            response = client.get("/v1/state", headers={"Cookie": "core_session=" + token})
            assert response.status_code == 200, response.text
            assert [p["id"] for p in response.json()["connections"]["value"]] == ["ark"]
            assert set(response.json()["defaults"]["value"]) == {"video_generation"}
        for tenant in app.state.account_services.apps.values():
            assert tenant.state.runtime.running == {}


def test_legacy_import_skips_retired_connections_models_and_credentials(tmp_path):
    store = Store(tmp_path / "state")
    access, transfer = Access(tmp_path / "access"), Fernet(Fernet.generate_key())
    owner, source = str(uuid4()), str(uuid4())
    rows = {
        "model_providers": [
            {
                "id": "retired",
                "owner_user_id": owner,
                "name": "Old provider",
                "kind": "senseaudio_asr_pro",
                "api_key_ciphertext": transfer.encrypt(b"old-secret").decode(),
            }
        ],
        "model_entries": [{"id": "old-model", "provider_id": "retired"}],
        "model_account_defaults": [
            {"owner_user_id": owner, "model_ids": {"speech_recognition": "old-model"}}
        ],
    }
    try:
        catalog, secrets = import_catalog(store, access, rows, owner, source, transfer, {})
        assert catalog.saved()["providers"] == []
        assert catalog.saved()["default_model_ids"] == {}
        assert secrets == ["old-secret"]  # In-memory redaction input, never stored.
        assert (
            store.db.execute(
                "SELECT COUNT(*) FROM configs WHERE id LIKE 'credential:%'"
            ).fetchone()[0]
            == 0
        )
    finally:
        store.close()
