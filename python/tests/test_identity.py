from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from dsh_core_worker.accounts import Accounts
from dsh_core_worker.contracts import CoreError
from dsh_core_worker.service import create_app


@pytest.fixture
def service(tmp_path):
    app = create_app(tmp_path / "core", origin="http://testserver")
    with TestClient(app) as client:
        result = client.post(
            "/v1/session",
            json={"ticket": app.state.access.ticket()},
            headers={"Origin": "http://testserver"},
        )
        assert result.status_code == 200
        client.headers.update({"Origin": "http://testserver", "X-Core-CSRF": result.json()["csrf"]})
        yield app, client


def test_passwords_one_use_invites_and_persistent_sessions(tmp_path):
    accounts = Accounts(tmp_path / "accounts")
    invite = accounts.invitation()
    user = accounts.create("alpha", "Alpha", "isolated-password-123", invite=invite)
    with pytest.raises(CoreError):
        accounts.create("beta", "Beta", "isolated-password-123", invite=invite)
    with pytest.raises(CoreError):
        accounts.login("alpha", "wrong-password-123")
    token, _ = accounts.login("alpha", "isolated-password-123")
    assert Accounts(tmp_path / "accounts").session(token)["account_id"] == user["id"]
    assert "password" not in user
    accounts.reset_password(user["id"], "new-isolated-password")
    assert accounts.session(token) is None
    assert accounts.login("alpha", "new-isolated-password")


def test_accounts_have_separate_configuration_and_admin_cannot_read_other_account(service):
    app, client = service
    new = client.post(
        "/v1/identity/accounts",
        json={"username": "beta", "display_name": "Beta", "password": "isolated-password-123"},
    ).json()
    assert (
        client.post(
            "/v1/rpc",
            json={
                "method": "config.save",
                "params": {
                    "key": "settings",
                    "revision": 0,
                    "value": {"temporary_upload_url": "https://uploads.example.test"},
                },
            },
        ).status_code
        == 200
    )
    client.cookies.clear()
    result = client.post(
        "/v1/identity/login", json={"username": "beta", "password": "isolated-password-123"}
    )
    assert result.status_code == 200
    client.headers["X-Core-CSRF"] = result.json()["csrf"]
    assert client.get("/v1/identity/accounts").status_code == 403
    state = client.get("/v1/state").json()
    assert "uploads.example.test" not in str(state)
    assert client.post("/v1/code-agents/detect", json={}).status_code == 403
    assert client.get("/v1/identity/me").json()["account"]["id"] == new["id"]


def test_application_code_is_one_use_audience_bound_and_revocation_is_live(service):
    app, client = service
    registration = client.post(
        "/v1/identity/applications",
        json={
            "id": "dsh-thought-workshop",
            "origin": "http://127.0.0.1:19363",
            "callback": "/apps/thought-workshop",
        },
    )
    assert registration.status_code == 200
    value = registration.json()
    state = "isolated-state-123456"
    response = client.get(
        "/v1/identity/authorize",
        params={"application_id": value["id"], "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 303
    code = parse_qs(urlsplit(response.headers["location"]).fragment)["code"][0]
    backend = TestClient(app)
    if True:
        headers = {"Authorization": "Bearer " + value["token"]}
        data = {"application_id": value["id"], "code": code}
        result = backend.post("/v1/identity/exchange", json=data, headers=headers)
        assert result.status_code == 200
        token = result.json()["token"]
        assert backend.post("/v1/identity/exchange", json=data, headers=headers).status_code == 401
        assert app.state.accounts.session(token) is None
        body = {"application_id": value["id"], "session": token}
        assert (
            backend.post("/v1/identity/introspect", json=body, headers=headers).status_code == 200
        )
        assert (
            backend.post(
                "/v1/identity/introspect",
                json=body,
                headers={**headers, "Origin": "http://evil.example"},
            ).status_code
            == 403
        )
        assert client.delete("/v1/identity/authorizations/" + value["id"]).status_code == 200
        assert (
            backend.post("/v1/identity/introspect", json=body, headers=headers).status_code == 401
        )


def test_disabling_account_revokes_sessions_and_bound_bridges(service):
    app, client = service
    user = client.post(
        "/v1/identity/accounts",
        json={"username": "gamma", "display_name": "Gamma", "password": "isolated-password-123"},
    ).json()
    accounts = app.state.accounts
    token, _ = accounts.issue(user["id"])
    accounts.register_client(
        "dsh-galgame-workshop", "http://127.0.0.1:19364", "/apps/galgame-workshop"
    )
    accounts.grant(user["id"], "dsh-galgame-workshop")
    accounts.bind_bridge(user["id"], "dsh-galgame-workshop", "isolated-bridge")
    accounts.authorize_bridge(user["id"], "isolated-bridge")
    assert (
        client.patch(
            "/v1/identity/accounts/" + user["id"], json={"active": False, "expected_revision": 0}
        ).status_code
        == 200
    )
    assert accounts.session(token) is None
    with pytest.raises(CoreError):
        accounts.authorize_bridge(user["id"], "isolated-bridge")
    assert (
        client.patch(
            "/v1/identity/accounts/" + user["id"], json={"active": True, "expected_revision": 0}
        ).status_code
        == 409
    )
    assert client.get("/v1/openapi.json").json()["paths"]["/v1/identity/login"]["post"][
        "requestBody"
    ]


def test_revoking_application_cancels_only_its_active_tasks(service):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from dsh_core_worker.bridges import Bridges

    app, client = service
    accounts = app.state.accounts
    owner = accounts.initial["id"]
    accounts.register_client(
        "dsh-thought-workshop", "http://127.0.0.1:19363", "/apps/thought-workshop"
    )
    accounts.grant(owner, "dsh-thought-workshop")
    accounts.bind_bridge(owner, "dsh-thought-workshop", "thought-bridge")
    target = Bridges.scope("thought-bridge", "thought-workshop")
    runtime = SimpleNamespace(
        store=SimpleNamespace(
            tasks=lambda **kwargs: [
                {"id": "writing-job", "request": {"application_id": target}},
                {"id": "other-app-job", "request": {"application_id": "other-application"}},
            ]
        ),
        invoke=AsyncMock(),
    )
    tenant = app.state.account_services.apps[owner]
    original = tenant.state.runtime
    tenant.state.runtime = runtime
    try:
        response = client.delete("/v1/identity/authorizations/dsh-thought-workshop")
        assert response.status_code == 200, response.text
        runtime.invoke.assert_awaited_once_with("task.cancel", {"id": "writing-job"})
    finally:
        tenant.state.runtime = original


def test_backend_public_directory_requires_live_account_application_grant(service):
    app, client = service
    value = client.post(
        "/v1/identity/applications",
        json={
            "id": "dsh-thought-workshop",
            "origin": "http://127.0.0.1:19363",
            "callback": "/apps/thought-workshop",
        },
    ).json()
    owner = app.state.accounts.initial["id"]
    body = {"application_id": value["id"], "owner_user_id": owner, "ids": [owner], "usernames": []}
    backend = TestClient(app)
    headers = {"Authorization": "Bearer " + value["token"]}
    response = backend.post("/v1/identity/directory", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["caller"]["id"] == owner
    assert set(response.json()["profiles"][0]) == {"id", "username", "display_name", "active"}
    other = client.post(
        "/v1/identity/accounts",
        json={
            "username": "directory-other",
            "display_name": "Other",
            "password": "isolated-password-123",
        },
    ).json()["id"]
    assert (
        backend.post(
            "/v1/identity/directory", json={**body, "owner_user_id": other}, headers=headers
        ).status_code
        == 403
    )
    assert client.delete("/v1/identity/authorizations/" + value["id"]).status_code == 200
    assert backend.post("/v1/identity/directory", json=body, headers=headers).status_code == 403
