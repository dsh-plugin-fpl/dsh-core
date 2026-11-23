import base64
import io
import json
import zipfile
from pathlib import Path
from typing import ClassVar

import pytest
from dsh_core_worker.integrations.portable import PortableCatalog, PortableIntegration
from dsh_core_worker.service import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def service(tmp_path):
    app = create_app(tmp_path / "core", origin="http://testserver")
    with TestClient(app) as client:
        auth = client.post(
            "/v1/session",
            json={"ticket": app.state.access.ticket()},
            headers={"Origin": "http://testserver"},
        ).json()
        client.headers.update({"Origin": "http://testserver", "X-Core-CSRF": auth["csrf"]})
        yield app, client


def archive(name="test-skill"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        z.writestr(
            "SKILL.md",
            f"---\nname: {name}\ndescription: Test skill\n---\n# Instructions\nRead the reference.",
        )
        z.writestr("references/example.txt", "Skill attachment")
    return output.getvalue()


def test_original_catalogs_crud_versions_credentials_and_owner(service):
    app, client = service
    for kind in ["tool-apis", "mcp", "skills", "external-connections"]:
        response = client.get("/v1/integrations/" + kind)
        assert response.status_code == 200, response.text
    tool = client.post(
        "/v1/integrations/tool-apis",
        json={
            "name": "Local API",
            "method": "POST",
            "url": "http://127.0.0.1:19320/tool",
            "auth_kind": "bearer",
            "secret": "integration-secret",
            "parameters_schema": {"type": "object"},
        },
    )
    assert tool.status_code == 201, tool.text
    row = tool.json()
    assert "integration-secret" not in tool.text
    updated = client.patch(
        "/v1/integrations/tool-apis/" + row["id"],
        json={
            "name": "Edited API",
            "method": "POST",
            "url": "http://127.0.0.1:19320/tool",
            "parameters_schema": {"type": "object"},
            "expected_revision": row["revision"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert (
        client.patch(
            "/v1/integrations/tool-apis/" + row["id"],
            json={
                "name": "Stale",
                "method": "GET",
                "url": "http://127.0.0.1:19320",
                "expected_revision": row["revision"],
            },
        ).status_code
        == 409
    )
    mcp = client.post(
        "/v1/integrations/mcp",
        json={
            "name": "MCP",
            "transport": "stdio",
            "command": "python",
            "arguments": ["fixture.py"],
            "environment": {"TOKEN": "env-secret"},
            "cwd": "/tmp",
            "headers": {"X-Key": "header-secret"},
            "timeout_ms": 45000,
            "enabled": False,
        },
    )
    assert mcp.status_code == 201, mcp.text
    assert mcp.json()["cwd"] == "/tmp"
    assert mcp.json()["timeout_ms"] == 45000
    assert "env-secret" not in mcp.text and "header-secret" not in mcp.text
    skill = client.post(
        "/v1/integrations/skills",
        files={"file": ("skill.zip", archive(), "application/zip")},
        data={"enabled": "false", "tags": ["example"]},
    )
    assert skill.status_code == 201, skill.text
    saved = skill.json()
    path = "/v1/integrations/skills/" + saved["id"] + "/versions/v1"
    assert "SKILL.md" in client.get(path + "/files").text
    assert (
        client.get(path + "/files/content", params={"path": "references/example.txt"}).status_code
        == 200
    )
    assert client.get(path + "/files/content", params={"path": "../secret.key"}).status_code == 422
    new = client.post(
        "/v1/integrations/skills/" + saved["id"] + "/versions",
        files={"file": ("skill.zip", archive(), "application/zip")},
        data={"expected_revision": saved["revision"]},
    )
    assert new.status_code == 200, new.text
    external = client.post(
        "/v1/integrations/external-connections",
        json={
            "name": "Feishu",
            "provider": "feishu",
            "app_id": "cli_example",
            "app_secret": "feishu-secret",
            "enabled": False,
        },
    )
    assert external.status_code == 201, external.text
    assert "feishu-secret" not in external.text
    assert b"integration-secret" not in app.state.integrations.database.path.read_bytes()
    assert (
        client.get("/settings", follow_redirects=False).headers["location"]
        == "/integrations?kind=external-connections"
    )
    client.cookies.clear()
    assert client.get("/v1/integrations/tool-apis").status_code == 401


async def test_portable_copy_atomic_receipt_full_skill_and_mcp_headers(tmp_path):
    from dsh_core_worker.access import Access
    from dsh_core_worker.integrations.assembly import Integrations
    from dsh_core_worker.runtime import Runtime

    async def host(*_):
        return None

    async def notify(*_):
        return None

    runtime = Runtime(tmp_path / "state", host, notify)
    manager = Integrations(tmp_path / "integrations", runtime, Access(tmp_path / "access"))
    await manager.database.migrate()
    try:
        portable = PortableCatalog(manager)
        item = PortableIntegration(
            kind="mcp",
            id="source",
            name="MCP copy",
            revision="1",
            config={
                "name": "MCP copy",
                "transport": "streamable_http",
                "url": "http://127.0.0.1:19320/mcp",
                "headers": {"X-Key": "copy-key"},
                "enabled": True,
            },
        )
        result = await portable.apply(
            item,
            target_id="a4db7f18-4a45-41f7-93c1-b0ba46f61da1",
            expected_revision="",
            create=True,
            receipt_id="copy/1",
        )
        assert (
            await portable.apply(
                item, target_id=result["id"], expected_revision="", create=True, receipt_id="copy/1"
            )
            == result
        )
        saved = await portable.read("mcp", result["id"])
        assert saved.config["headers"]["X-Key"] == "copy-key"
        assert saved.config["enabled"] is False
        assert len(await portable.list()) == 1
        skill = PortableIntegration(
            kind="skills",
            id="source-skill",
            name="test-skill",
            revision="1",
            config={"name": "test-skill", "tags": []},
            archive_base64=base64.b64encode(archive()).decode(),
        )
        result = await portable.apply(
            skill,
            target_id="eb06a9cf-a84c-432f-a98c-87a66c9176eb",
            expected_revision="",
            create=True,
            receipt_id="copy/2",
        )
        copied = await portable.read("skills", result["id"])
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(copied.archive_base64))) as z:
            assert z.read("references/example.txt") == b"Skill attachment"
    finally:
        await manager.close()
        await runtime.close()


def test_tool_validation_real_history_and_no_credentials_in_receipts(service):
    import httpx

    app, client = service
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"echo": "Bearer wire-secret"})

    app.state.integrations.tool_transport = httpx.MockTransport(respond)
    tool = client.post(
        "/v1/integrations/tool-apis",
        json={
            "name": "Validation",
            "method": "POST",
            "url": "http://127.0.0.1:19320/tool",
            "auth_kind": "bearer",
            "secret": "wire-secret",
            "parameters_schema": {"type": "object"},
            "body_template": {"message": "{{message}}"},
        },
    ).json()
    assert seen == []
    response = client.post(
        "/v1/integrations/tool-apis/" + tool["id"] + "/validate",
        json={"input": {"message": "hello"}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["check"]["ok"]
    assert seen[0].headers["Authorization"] == "Bearer wire-secret"
    assert "wire-secret" not in response.text
    tasks = client.get("/v1/tasks").json()["items"]
    assert tasks[0]["request"]["kind"] == "integration"
    assert "wire-secret" not in json.dumps(tasks)
    assert client.get("/v1/integrations/tool-apis").json()["overview"]["recent"]


def test_mcp_stdio_discovery_cwd_environment_and_tool_call(service, tmp_path):
    import sys

    _app, client = service
    fixture = Path(__file__).parent / "fixtures/mcp_echo.py"
    mcp = client.post(
        "/v1/integrations/mcp",
        json={
            "name": "Stdio fixture",
            "transport": "stdio",
            "command": sys.executable,
            "arguments": [str(fixture.resolve())],
            "cwd": str(tmp_path),
            "environment": {"FIXTURE_SECRET": "do-not-show"},
        },
    ).json()
    response = client.post("/v1/integrations/mcp/" + mcp["id"] + "/validate")
    assert response.status_code == 200, response.text
    assert response.json()["check"]["ok"], response.text
    assert len(client.get("/v1/integrations/mcp/" + mcp["id"] + "/tools").json()) == 1
    assert "do-not-show" not in response.text
    call = {
        "request_id": "mcp-once",
        "expected_revision": mcp["revision"],
        "tool_name": "echo",
        "input": {"text": "hello"},
    }
    first = client.post("/v1/integrations/mcp/" + mcp["id"] + "/call", json=call)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "completed", first.text
    assert first.json()["request"]["kind"] == "integration"
    assert "do-not-show" not in first.text
    assert (
        client.post("/v1/integrations/mcp/" + mcp["id"] + "/call", json=call).json()["id"]
        == first.json()["id"]
    )
    assert (
        client.post(
            "/v1/integrations/mcp/" + mcp["id"] + "/call",
            json={**call, "input": {"text": "changed"}},
        ).status_code
        == 409
    )


@pytest.mark.parametrize("name", ["../unsafe", "/unsafe"])
def test_unsafe_skill_archive_rejected_without_publication(service, name):
    _app, client = service
    value = io.BytesIO()
    with zipfile.ZipFile(value, "w") as z:
        z.writestr("SKILL.md", "---\nname: safe-skill\ndescription: Test\n---\nText")
        z.writestr(name, "unsafe")
    response = client.post(
        "/v1/integrations/skills",
        files={"file": ("unsafe.zip", value.getvalue(), "application/zip")},
    )
    assert response.status_code == 422
    assert client.get("/v1/integrations/skills").json()["items"] == []


async def test_transfer_partial_conflicts_receipt_recovery_and_skill_update(tmp_path):
    from types import SimpleNamespace

    from dsh_core_worker.access import Access
    from dsh_core_worker.contracts import CoreError
    from dsh_core_worker.integrations.assembly import Integrations
    from dsh_core_worker.integrations.contracts.catalog import ToolApiUpdateIn
    from dsh_core_worker.integrations.transfers import Choice, PreviewIn, Transfers
    from dsh_core_worker.runtime import Runtime

    async def noop(*_):
        return None

    access = Access(tmp_path / "access")
    runtime = Runtime(tmp_path / "state", noop, noop)
    manager = Integrations(tmp_path / "integrations", runtime, access)
    await manager.database.migrate()
    records = {
        key: PortableIntegration(
            kind="tool-apis",
            id=key,
            name="Source " + key,
            revision="1",
            config={
                "url": "http://127.0.0.1:1234",
                "method": "GET",
                "secret": "source-secret",
                "auth_kind": "bearer",
            },
        )
        for key in ("one", "two")
    }

    class Bridge:
        id = "bridge"
        integration_adapters: ClassVar[list[dict]] = [
            {
                "id": "adapter",
                "kinds": ["tool-apis", "skills"],
                "can_import": True,
                "can_export": True,
            }
        ]
        imports = 0
        receipt: ClassVar[dict] = {}

        async def call(self, method, params):
            if method == "integrations.list":
                return [
                    {"id": v.id, "kind": v.kind, "name": v.name, "revision": v.revision}
                    for v in records.values()
                ]
            if method == "integrations.export":
                return records[params["id"]].model_dump()
            if method == "integrations.import":
                self.imports += 1
                self.receipt = {
                    "status": "completed",
                    "result": {
                        "id": params["target_id"],
                        "kind": "tool-apis",
                        "name": params["item"]["name"],
                        "revision": "0",
                    },
                }
                raise CoreError("Lost response", "bridge_disconnected")
            if method == "integrations.receipt":
                return self.receipt

    bridge = Bridge()
    bridges = SimpleNamespace(online={"bridge": bridge})
    transfers = Transfers(manager, bridges, access)

    def body(items, direction="from-dsh"):
        return PreviewIn(direction=direction, bridge_id="bridge", adapter_id="adapter", items=items)

    def choices(plan, action="create"):
        return [Choice(key=row["key"], action=action) for row in plan["rows"]]

    try:
        plan = await transfers.preview(body([{"kind": "tool-apis", "id": key} for key in records]))
        records["two"].revision = "2"
        applied = await transfers.apply(plan["id"], choices(plan))
        assert applied["status"] == "partial"
        assert applied["rows"][0]["status"] == "completed"
        assert applied["rows"][1]["status"] == "failed"
        await transfers.apply(plan["id"], choices(plan))
        assert len(await transfers.portable.list()) == 1
        target = applied["rows"][0]["result"]
        plan = await transfers.preview(body([{"kind": "tool-apis", "id": "one"}]))
        assert plan["rows"][0]["action"] == "update"
        async with manager.database.sessions() as session:
            await manager.tool_catalog(session).update(
                target["id"],
                ToolApiUpdateIn(
                    name="Owner edited",
                    url="http://127.0.0.1:1234",
                    expected_revision=int(target["revision"]),
                ),
            )
        conflict = await transfers.apply(plan["id"], choices(plan, "update"))
        assert conflict["rows"][0]["status"] == "failed"
        plan = await transfers.preview(body([{"kind": "tool-apis", "id": "one"}]))
        assert plan["rows"][0]["action"] == "skip"
        # Unknown destination response is resolved only by querying its original receipt.
        plan = await transfers.preview(
            body([{"kind": "tool-apis", "id": target["id"]}], direction="to-dsh")
        )
        first = await transfers.apply(plan["id"], choices(plan))
        assert first["rows"][0]["status"] == "unknown"
        with pytest.raises(CoreError):
            await transfers.apply(plan["id"], choices(plan, "skip"))
        transfers = Transfers(manager, bridges, access)
        recovered = await transfers.apply(plan["id"], choices(plan))
        assert recovered["status"] == "completed"
        assert bridge.imports == 1
        mapping = transfers.store.config(
            transfers.map_key("to-dsh", "bridge", "adapter", "tool-apis", target["id"]), {}
        )["value"]
        assert mapping["id"] == recovered["rows"][0]["result"]["id"]
        # A complete Skill can be updated without losing versioned attachments.
        item = PortableIntegration(
            kind="skills",
            id="skill",
            name="test-skill",
            revision="1",
            config={"name": "test-skill"},
            archive_base64=base64.b64encode(archive()).decode(),
        )
        records["skill"] = item
        plan = await transfers.preview(body([{"kind": "skills", "id": "skill"}]))
        created = await transfers.apply(plan["id"], choices(plan))
        assert created["status"] == "completed"
        plan = await transfers.preview(body([{"kind": "skills", "id": "skill"}]))
        assert "Skill 文件包" not in plan["rows"][0]["changes"]
        updated = await transfers.apply(plan["id"], choices(plan, "update"))
        assert updated["status"] == "completed", updated
        # An explicit new copy gets a valid name in the archive as well.
        plan = await transfers.preview(body([{"kind": "skills", "id": "skill"}]))
        newcopy = await transfers.apply(plan["id"], choices(plan, "create"))
        assert newcopy["status"] == "completed", newcopy
        catalog = [x for x in await transfers.portable.list() if x["kind"] == "skills"]
        assert len(catalog) == 2
        assert len({x["name"] for x in catalog}) == 2
    finally:
        await manager.close()
        await runtime.close()
