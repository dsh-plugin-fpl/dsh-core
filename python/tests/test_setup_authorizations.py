import hashlib

import pytest
from fastapi.testclient import TestClient

from dsh_core_worker.service import create_app


@pytest.fixture
def setup_service(tmp_path):
    app = create_app(tmp_path / "core", origin="http://testserver")
    with TestClient(app) as client:
        response = client.post("/v1/session", json={"ticket": app.state.access.ticket()}, headers={"Origin": "http://testserver"})
        client.headers.update({"Origin": "http://testserver", "X-Core-CSRF": response.json()["csrf"]})
        yield app, client, TestClient(app)


def begin(backend, application="dsh-thought-workshop", **extra):
    proof = "isolated-host-proof-" + "a" * 48
    payload = {"profile": "isolated-profile", "challenge": hashlib.sha256(proof.encode()).hexdigest(),
               "applications": [{"id": application, "origin": "http://127.0.0.1:19363", **extra}]}
    response = backend.post("/v1/setup/authorizations", json=payload)
    assert response.status_code == 200
    return response.json()["id"], proof


def test_browser_approval_and_proof_receipt_are_separate_and_idempotent(setup_service):
    app, browser, backend = setup_service
    identity, proof = begin(backend)
    path = "/v1/setup/authorizations/" + identity
    assert backend.get(path).status_code == 401
    assert backend.post(path + "/approve").status_code == 403
    assert backend.post(path + "/exchange", json={"verifier": proof}).json() == {"status": "pending"}
    assert backend.post(path + "/exchange", json={"verifier": "wrong-" * 12}).status_code == 401
    approved = browser.post(path + "/approve")
    assert approved.status_code == 200
    assert "token" not in str(approved.json())
    first = backend.post(path + "/exchange", json={"verifier": proof})
    assert first.status_code == 200
    receipt = first.json()
    assert receipt["status"] == "approved"
    assert receipt["applications"]["dsh-thought-workshop"]["client"]["token"]
    assert browser.post(path + "/approve").status_code == 200
    assert backend.post(path + "/exchange", json={"verifier": proof}).json() == receipt
    assert app.state.accounts.has_grant(receipt["account_id"], "dsh-thought-workshop")
    app.state.accounts.revoke_application(receipt["account_id"], "dsh-thought-workshop")
    assert backend.post(path + "/exchange", json={"verifier": proof}).status_code == 403


def test_account_and_origin_mismatch_never_issue_a_grant(setup_service):
    app, browser, backend = setup_service
    identity, proof = begin(backend, expected_account="another-account")
    path = "/v1/setup/authorizations/" + identity
    assert browser.post(path + "/approve").status_code == 403
    assert backend.post(path + "/exchange", json={"verifier": proof}).json() == {"status": "pending"}
    payload = {"profile": "isolated", "challenge": "a" * 64,
               "applications": [{"id": "dsh-thought-workshop", "origin": "https://example.com"}]}
    assert backend.post("/v1/setup/authorizations", json=payload).status_code == 422


def test_core_only_setup_cannot_replace_another_accounts_connection(setup_service):
    _, browser, backend = setup_service
    proof = "isolated-proof-" + "a" * 48
    created = backend.post("/v1/setup/authorizations", json={"profile": "existing-profile", "challenge": hashlib.sha256(proof.encode()).hexdigest(),
        "applications": [], "expected_account": "another-account"})
    assert created.status_code == 200
    path = "/v1/setup/authorizations/" + created.json()["id"]
    assert browser.post(path + "/approve").status_code == 403
    assert backend.post(path + "/exchange", json={"verifier": proof}).json() == {"status": "pending"}


def test_setup_requires_valid_csrf_and_reuses_existing_clients(setup_service):
    _, browser, backend = setup_service
    first, proof = begin(backend)
    response = browser.post("/v1/setup/authorizations/" + first + "/approve", headers={"X-Core-CSRF": "invalid"})
    assert response.status_code == 403
    assert browser.post("/v1/setup/authorizations/" + first + "/approve").status_code == 200
    second, proof = begin(backend)
    assert browser.post("/v1/setup/authorizations/" + second + "/approve").status_code == 200
    receipt = backend.post("/v1/setup/authorizations/" + second + "/exchange", json={"verifier": proof}).json()
    assert receipt["applications"]["dsh-thought-workshop"]["client"] is None


def test_partial_issuance_resumes_the_persisted_secret_and_bridge(setup_service, monkeypatch):
    app, browser, backend = setup_service
    identity, proof = begin(backend)
    path = "/v1/setup/authorizations/" + identity
    original = app.state.access.ensure_pair
    interrupted = False

    def issue(receipt):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise RuntimeError("isolated issuance interruption")
        return original(receipt)

    monkeypatch.setattr(app.state.access, "ensure_pair", issue)
    with pytest.raises(RuntimeError, match="issuance interruption"):
        browser.post(path + "/approve")
    assert browser.post(path + "/approve").status_code == 200
    receipt = backend.post(path + "/exchange", json={"verifier": proof}).json()
    assert receipt["applications"]["dsh-thought-workshop"]["client"]["token"]
    assert len(app.state.access.bridges()) == 2
