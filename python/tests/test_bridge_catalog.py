from fastapi.testclient import TestClient

from dsh_core_worker.service import create_app


def test_bridge_catalog_distinguishes_application_identity_from_dsh_pairing(tmp_path):
    root = tmp_path / "core"
    app = create_app(root, origin="http://testserver")
    with TestClient(app) as client:
        login = client.post(
            "/v1/session",
            json={"ticket": app.state.access.ticket()},
            headers={"Origin": "http://testserver"},
        ).json()
        client.headers.update({"Origin": "http://testserver", "X-Core-CSRF": login["csrf"]})
        # Names and online state do not determine the connection's purpose.
        host = client.post("/v1/bridges", json={"name": "视觉创作工坊"}).json()
        application = client.post("/v1/bridges", json={"name": "DeepSeek Harness"}).json()
        registered = client.post(
            "/v1/identity/applications",
            json={
                "id": "dsh-thought-workshop",
                "origin": "http://127.0.0.1:19363",
                "callback": "/apps/thought-workshop",
                "bridge_id": application["id"],
            },
        )
        assert registered.status_code == 200, registered.text
        with client.websocket_connect(
            "/v1/bridge", headers={"Authorization": "Bearer " + application["token"]}
        ) as socket:
            socket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "register",
                    "method": "application.register",
                    "params": {
                        "id": "thought-workshop",
                        "name": "思绪工坊",
                        "tools": [],
                        "dashboard": {"category": "knowledge", "url": "http://127.0.0.1:19363/"},
                    },
                }
            )
            assert "error" not in socket.receive_json()
            response = client.get("/v1/bridges")
            rows = {row["id"]: row for row in response.json()["items"]}
            assert rows[host["id"]] == {
                "id": host["id"],
                "name": host["name"],
                "connected": False,
                "kind": "dsh",
                "application_id": None,
            }
            assert rows[application["id"]]["kind"] == "application"
            assert rows[application["id"]]["application_id"] == "dsh-thought-workshop"
            assert rows[application["id"]]["connected"] is True
            assert "token" not in response.text and "digest" not in response.text
            entries = client.get("/v1/applications").json()["items"]
            assert entries[0]["name"] == "思绪工坊" and entries[0]["status"] == "ready"

        # Classification survives disconnection, revoked grants and service restart.
        assert client.delete("/v1/identity/authorizations/dsh-thought-workshop").status_code == 200

    restarted = create_app(root, origin="http://testserver")
    rows = {row["id"]: row for row in restarted.state.access.bridges()}
    assert rows[application["id"]]["kind"] == "application"
    assert rows[host["id"]]["kind"] == "dsh"


def test_application_bridge_classification_is_account_scoped(tmp_path):
    app = create_app(tmp_path / "core", origin="http://testserver")
    accounts = app.state.accounts
    owner = accounts.initial["id"]
    other = accounts.create("other", "Other", "isolated-password-123")
    accounts.register_client("notes", "http://127.0.0.1:19363", "/apps/notes")
    accounts.bind_bridge(other["id"], "notes", "same-local-id")
    app.state.access.ensure_pair({"id": "same-local-id", "name": "DSH", "token": "fixture"})
    assert app.state.access.bridges()[0]["kind"] == "dsh"
    assert accounts.bridge_applications(owner) == {}
    assert accounts.bridge_applications(other["id"]) == {"same-local-id": "notes"}
