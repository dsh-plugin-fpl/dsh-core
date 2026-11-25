import base64
import hashlib
import io
import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from dsh_core_worker.service import create_app


def skill_zip():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "SKILL.md",
            "---\nname: fixture-skill\ndescription: A fixture skill\n---\nUse this fixture.\n",
        )
        z.writestr("assets/example.txt", "complete attachment")
    return output.getvalue()


def part(kind, identity, manifest=None):
    raw = skill_zip() if kind == "skill" else b""
    return {
        "release": {
            "id": identity,
            "kind": kind,
            "version": 1,
            "title": identity,
            "summary": "Fixture",
            "manifest": manifest or {},
            "tags": [],
            "installable": True,
        },
        "archive_base64": base64.b64encode(raw).decode(),
        "archive_sha256": hashlib.sha256(raw).hexdigest(),
    }


@pytest.fixture
def connected(tmp_path):
    app = create_app(tmp_path / "core", origin="http://testserver")
    fixture = {
        "account": "alice",
        "fail": False,
        "withdrawn": False,
        "calls": [],
        "parts": [
            part(
                "tool_api",
                "weather",
                {"url": "https://weather.example/api", "method": "GET", "auth_kind": "bearer"},
            ),
            part(
                "mcp",
                "remote-mcp",
                {"transport": "streamable_http", "url": "https://mcp.example/mcp"},
            ),
            part("skill", "fixture-skill", {"name": "fixture-skill"}),
            part("mcp", "stdio-mcp", {"transport": "stdio", "command_hint": "install fixture"}),
        ],
    }

    def remote(request):
        fixture["calls"].append(request)
        if fixture["fail"]:
            raise httpx.ConnectError("offline", request=request)
        path = request.url.path
        if path == "/v1/auth/login":
            body = json.loads(request.content)
            if body["password"] != "fixture-password":
                return httpx.Response(401, json={"detail": {"message": "invalid"}})
            fixture["account"] = body["username"]
            return httpx.Response(
                200,
                json={
                    "user": {
                        "id": body["username"],
                        "username": body["username"],
                        "display_name": "Fixture",
                    },
                    "permission_keys": ["content.read", "content.write", "configuration.manage"],
                },
                headers=[
                    ("Set-Cookie", "fpl_session=fixture-session; HttpOnly; Path=/"),
                    ("Set-Cookie", "fpl_csrf=fixture-csrf; Path=/"),
                ],
            )
        assert "fpl_session=fixture-session" in request.headers.get("cookie", "")
        if request.method != "GET":
            assert request.headers["origin"] == "http://127.0.0.1:3000"
            assert request.headers["x-csrf-token"] == "fixture-csrf"
        if path == "/v1/auth/me":
            return httpx.Response(
                200,
                json={
                    "user": {"id": fixture["account"], "username": fixture["account"]},
                    "permission_keys": ["content.read", "content.write", "configuration.manage"],
                },
            )
        if path == "/v1/auth/logout":
            return httpx.Response(204)
        if path.endswith("/package"):
            if fixture["withdrawn"]:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "schema_version": 1,
                    "release_id": path.split("/")[-2],
                    "parts": fixture["parts"],
                },
            )
        if path == "/v1/creative-workshop/items":
            if request.method == "POST":
                return httpx.Response(
                    200, json={**json.loads(request.content), "id": "draft-id", "revision": 0}
                )
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        return httpx.Response(404)

    with TestClient(app) as client:
        assert client.post("/v1/session/local").status_code == 403
        response = client.post("/v1/session/local", headers={"Origin": "http://testserver"})
        assert response.status_code == 200
        client.headers.update(
            {"Origin": "http://testserver", "X-Core-CSRF": response.json()["csrf"]}
        )
        website = app.state.account_services.initial.state.fpl_website
        website.transport = httpx.MockTransport(remote)
        result = client.post(
            "/v1/fpl-connection/login", json={"username": "alice", "password": "fixture-password"}
        )
        assert result.status_code == 200, result.text
        yield app, client, fixture


def test_local_bootstrap_preserves_existing_accounts_and_csrf(tmp_path):
    app = create_app(tmp_path / "core", origin="http://testserver")
    with TestClient(app) as client:
        result = client.post("/v1/session/local", headers={"Origin": "http://testserver"})
        assert result.json()["owner"]["username"] == "local-owner"
        assert client.get("/login", follow_redirects=False).headers["location"] == "/"
        assert 'name="password"' in client.get("/login?core=1").text
        assert "/v1/session/local" in app.openapi()["paths"]
        assert "HttpOnly" in result.headers.get_list("set-cookie")[0]
        client.headers.update({"Origin": "http://testserver", "X-Core-CSRF": result.json()["csrf"]})
        new = client.post(
            "/v1/identity/accounts",
            json={"username": "beta", "display_name": "Beta", "password": "fixture-password-123"},
        ).json()
        client.cookies.clear()
        result = client.post(
            "/v1/identity/login", json={"username": "beta", "password": "fixture-password-123"}
        )
        client.headers["X-Core-CSRF"] = result.json()["csrf"]
        assert client.post("/v1/session/local").json()["owner"]["id"] == new["id"]
        client.headers.pop("X-Core-CSRF")
        assert client.post("/v1/session/local").status_code == 403


def test_remote_identity_and_offline_do_not_change_local_data(connected):
    app, client, fixture = connected
    local = client.get("/v1/identity/me").json()
    assert local["account"]["username"] == "local-owner"
    response = client.get("/v1/fpl-connection")
    assert response.json()["account"]["id"] == "alice"
    assert "fixture-session" not in response.text and "fixture-csrf" not in response.text
    assert client.get("/v1/creative-workshop/items").status_code == 200
    assert client.post("/v1/creative-workshop/items", json={"title": "Fixture"}).status_code == 200
    fixture["fail"] = True
    assert client.get("/v1/creative-workshop/items").status_code == 502
    assert client.get("/v1/identity/me").json() == local
    assert client.get("/v1/fpl-connection").json()["offline"]
    assert "fixture-password" not in app.state.access.read("fpl-website.enc").values()


def preview_install(client):
    result = client.post(
        "/v1/creative-workshop/installations/preview", json={"release_id": "bundle"}
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_four_capabilities_install_disabled_with_receipt_and_complete_skill(connected):
    _, client, _fixture = connected
    preview = preview_install(client)
    assert preview["installable"]
    body = {"release_id": "bundle", "preview_id": preview["id"]}
    result = client.post("/v1/creative-workshop/installations", json=body)
    assert result.status_code == 200, result.text
    record = result.json()
    assert len(record["resources"]) == 4
    for kind in ("tool-apis", "mcp", "skills"):
        assert all(
            not row["enabled"] for row in client.get("/v1/integrations/" + kind).json()["items"]
        )
    assert client.post("/v1/creative-workshop/installations", json=body).json() == record
    assert client.get("/v1/creative-workshop/installations/" + preview["id"]).json() == record
    skill = next(r for r in record["resources"] if r["kind"] == "skill")
    assert skill["name"] == "fixture-skill"
    # The original attachments remain in the owning Skill directory regardless of download route naming.
    root = client.app.state.account_services.initial.state.integrations.root
    assert any(p.read_text() == "complete attachment" for p in root.rglob("example.txt"))
    response = client.post(
        f"/v1/creative-workshop/installations/{preview['id']}/stdio",
        json={"release_id": "stdio-mcp", "command": "fixture-command", "arguments": []},
    )
    assert response.status_code == 200, response.text
    assert len(client.get("/v1/integrations/mcp").json()["items"]) == 2
    assert (
        client.post(
            f"/v1/creative-workshop/installations/{preview['id']}/stdio",
            json={"release_id": "stdio-mcp", "command": "fixture-command"},
        ).status_code
        == 200
    )
    assert all(p["already_installed"] for p in preview_install(client)["parts"])


def test_name_conflicts_withdrawal_account_switch_and_batch_rollback(connected):
    _, client, fixture = connected
    preview = preview_install(client)
    fixture["withdrawn"] = True
    assert (
        client.post(
            "/v1/creative-workshop/installations",
            json={"release_id": "bundle", "preview_id": preview["id"]},
        ).status_code
        == 404
    )
    assert client.get("/v1/integrations/tool-apis").json()["items"] == []
    fixture["withdrawn"] = False
    client.post(
        "/v1/fpl-connection/login", json={"username": "bob", "password": "fixture-password"}
    )
    assert (
        client.post(
            "/v1/creative-workshop/installations",
            json={"release_id": "bundle", "preview_id": preview["id"]},
        ).status_code
        == 409
    )
    fixture["parts"][1]["release"]["manifest"]["url"] = "not-a-url"
    preview = preview_install(client)
    result = client.post(
        "/v1/creative-workshop/installations",
        json={"release_id": "bundle", "preview_id": preview["id"]},
    )
    assert result.status_code == 422, result.text
    assert client.get("/v1/integrations/tool-apis").json()["items"] == []
    assert client.get("/v1/integrations/skills").json()["items"] == []


def test_tampered_skill_rejected_and_local_publication_omits_secrets(connected):
    _, client, fixture = connected
    fixture["parts"][2]["archive_sha256"] = "invalid"
    assert (
        client.post(
            "/v1/creative-workshop/installations/preview", json={"release_id": "bundle"}
        ).status_code
        == 422
    )
    tool = client.post(
        "/v1/integrations/tool-apis",
        json={
            "name": "local-tool",
            "description": "Example",
            "method": "GET",
            "url": "https://tools.example/api",
            "auth_kind": "bearer",
            "secret": "never-publish-this",
            "enabled": False,
        },
    ).json()
    result = client.post(
        "/v1/creative-workshop/items/from-integration",
        json={"kind": "tool_api", "source_id": tool["id"]},
    )
    assert result.status_code == 200, result.text
    exported = fixture["calls"][-1].content
    assert b"never-publish-this" not in exported
    assert b'"secret"' not in exported


def test_expired_website_session_and_offline_logout_leave_local_identity(connected):
    app, client, _fixture = connected
    local = client.get("/v1/identity/me").json()
    remote = app.state.account_services.initial.state.fpl_website
    remote.transport = httpx.MockTransport(lambda request: httpx.Response(401))
    response = client.get("/v1/creative-workshop/items")
    assert response.status_code == 424
    assert response.headers["X-FPL-Auth-Required"] == "1"
    assert client.get("/v1/fpl-connection").json()["account"] is None
    assert client.get("/v1/identity/me").json() == local
    remote.transport = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("offline"))
    )
    assert client.post("/v1/fpl-connection/logout").status_code == 200
    assert client.get("/v1/identity/me").json() == local


def test_skill_files_are_compensated_when_later_batch_part_fails(connected):
    app, client, fixture = connected
    fixture["parts"] = [fixture["parts"][2], fixture["parts"][1]]
    fixture["parts"][1]["release"]["manifest"]["url"] = "not-a-url"
    plan = preview_install(client)
    response = client.post(
        "/v1/creative-workshop/installations",
        json={"release_id": "bundle", "preview_id": plan["id"]},
    )
    assert response.status_code == 422
    assert client.get("/v1/integrations/skills").json()["items"] == []
    assert not list(app.state.account_services.initial.state.integrations.root.rglob("example.txt"))


def test_connection_is_owned_by_local_account_and_survives_restart(connected):
    app, client, _fixture = connected
    remote = app.state.account_services.initial.state.fpl_website
    from dsh_core_worker.creative.connection import Website

    restored = Website(app.state.access)
    assert restored.load()["account"]["id"] == "alice"
    assert "fixture-session" not in (app.state.access.root / "fpl-website.enc").read_text()
    client.post(
        "/v1/identity/accounts",
        json={"username": "other", "display_name": "Other", "password": "fixture-password-123"},
    )
    client.cookies.clear()
    response = client.post(
        "/v1/identity/login", json={"username": "other", "password": "fixture-password-123"}
    )
    client.headers["X-Core-CSRF"] = response.json()["csrf"]
    assert client.get("/v1/fpl-connection").json()["account"] is None
    assert remote.load()["account"]["id"] == "alice"


def test_workshop_login_bootstraps_local_owner_and_preserves_authorization_boundary(tmp_path):
    app = create_app(tmp_path / "core", origin="http://testserver")
    with TestClient(app) as client:
        initial = client.post("/v1/session/local", headers={"Origin": "http://testserver"})
        client.headers.update(
            {"Origin": "http://testserver", "X-Core-CSRF": initial.json()["csrf"]}
        )
        registered = client.post(
            "/v1/identity/applications",
            json={
                "id": "dsh-thought-workshop",
                "origin": "http://127.0.0.1:19363",
                "callback": "/apps/thought-workshop",
            },
        )
        assert registered.status_code == 200
        client.cookies.clear()
        response = client.get(
            "/login?application_id=dsh-thought-workshop&state=fixture-state-123456"
        )
        assert "fetch('/v1/session/local'" in response.text
        assert "/v1/identity/authorize?" in response.text
        assert (
            client.get(
                "/v1/identity/authorize?application_id=dsh-thought-workshop&state=fixture-state-123456"
            ).status_code
            == 401
        )
        client.post("/v1/session/local")
        authorized = client.get(
            "/v1/identity/authorize?application_id=dsh-thought-workshop&state=fixture-state-123456",
            follow_redirects=False,
        )
        assert authorized.status_code == 303
        assert authorized.headers["location"].startswith(
            "http://127.0.0.1:19363/apps/thought-workshop#code="
        )
