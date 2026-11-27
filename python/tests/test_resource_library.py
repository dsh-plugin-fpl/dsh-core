"""Public GitHub fixtures exercise real account-owned catalog and integration installs."""

import hashlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from dsh_core_worker.resource_library.github import parse_url
from dsh_core_worker.service import create_app


def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


@pytest.fixture
def resources(tmp_path):
    skill = b"---\nname: fixture-skill\ndescription: A local fixture\n---\nUse this skill.\n"
    manifest = {
        "id": "fixture",
        "name": "Fixture",
        "type": "skill",
        "version": "1.0.0",
        "description": "A fixture",
        "license": "MIT",
        "status": "published",
        "format": "agent-skill",
        "entry": "SKILL.md",
        "files": ["SKILL.md", "assets/example.txt"],
    }
    remote = {
        "revision": "a" * 40,
        "offline": False,
        "bad_blob": False,
        "calls": [],
        "truncated": False,
        "files": {
            "source.json": json.dumps(
                {"schema_version": "0.1-draft", "source_id": "fixture.source"}
            ).encode(),
            "skills/fixture/resource.yaml": json.dumps(manifest).encode(),
            "skills/fixture/SKILL.md": skill,
            "skills/fixture/assets/example.txt": b"attachment",
        },
        "modes": {},
    }

    def network(request):
        remote["calls"].append(str(request.url))
        if remote["offline"]:
            raise httpx.ConnectError("offline", request=request)
        if remote.get("rate_limit"):
            return httpx.Response(429, headers={"retry-after": "60"})
        path = request.url.path
        if request.url.host == "api.github.com":
            if "/commits/" in path:
                return httpx.Response(
                    200, json={"sha": remote["revision"], "commit": {"tree": {"sha": "b" * 40}}}
                )
            if "/git/trees/" in path:
                return httpx.Response(
                    200,
                    json={
                        "truncated": remote["truncated"],
                        "tree": [
                            {
                                "path": name,
                                "mode": remote["modes"].get(name, "100644"),
                                "type": "blob",
                                "sha": blob(data),
                                "size": len(data),
                            }
                            for name, data in remote["files"].items()
                        ],
                    },
                )
            return httpx.Response(200, json={"private": False, "default_branch": "main"})
        prefix = "/test/resources/" + remote["revision"] + "/"
        assert path.startswith(prefix), path
        data = remote["files"].get(path[len(prefix) :])
        if data is None:
            return httpx.Response(404)
        return httpx.Response(200, content=b"tampered" if remote["bad_blob"] else data)

    app = create_app(tmp_path / "core", origin="http://testserver")
    with TestClient(app) as client:
        client.post("/v1/session/local", headers={"Origin": "http://testserver"})
        account = app.state.accounts.session(client.cookies["core_session"])
        client.headers.update({"Origin": "http://testserver", "x-core-csrf": account["csrf"]})
        app.state.resource_library.github.transport = httpx.MockTransport(network)
        yield client, app, remote, manifest


def add(client, url="https://github.com/test/resources"):
    response = client.post("/v1/resource-sources", json={"url": url})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def catalog(client, identity):
    response = client.get(f"/v1/resource-sources/{identity}/catalog")
    assert response.status_code == 200, response.text
    return response.json()


def apply(client, identity, cat, key=None, **extra):
    item = next(i for i in cat["items"] if i["key"] == key) if key else cat["items"][0]
    return client.post(
        "/v1/resource-library/imports",
        json={
            "source_id": identity,
            "revision": cat["revision"],
            "resource_key": item["key"],
            "digest": item["digest"],
            **extra,
        },
    )


def test_public_import_is_disabled_idempotent_and_persistent(resources):
    client, _app, remote, _ = resources
    source = add(client)
    cat = catalog(client, source)
    assert cat["revision"] == "a" * 40
    result = apply(client, source, cat)
    assert result.status_code == 200, result.text
    receipt = result.json()
    assert receipt["resources"][0]["enabled"] is False
    assert apply(client, source, cat).json() == receipt
    skills = client.get("/v1/integrations/skills").json()["items"]
    assert len(skills) == 1 and not skills[0]["enabled"]
    assert catalog(client, source)["items"][0]["installed"]
    assert client.get("/v1/resource-library/imports").json()["items"] == [receipt]
    assert all("/v1/auth/" not in c for c in remote["calls"])


def test_cached_catalog_and_import_work_offline(resources):
    client, _, remote, _ = resources
    source = add(client)
    cat = catalog(client, source)
    assert apply(client, source, cat).status_code == 200
    remote["offline"] = True
    refreshed = client.post(f"/v1/resource-sources/{source}/refresh").json()
    assert refreshed["items"] and refreshed["refresh_error"]
    assert apply(client, source, cat).status_code == 200


def test_digest_and_same_version_mutation_rejected(resources):
    client, _, remote, manifest = resources
    source = add(client)
    old = catalog(client, source)
    assert apply(client, source, old).status_code == 200
    remote["revision"] = "c" * 40
    remote["files"]["skills/fixture/assets/example.txt"] = b"new attachment"
    response = client.post(f"/v1/resource-sources/{source}/refresh")
    assert response.status_code == 200, response.text
    cat = response.json()
    assert cat["items"][0]["update_available"]
    assert apply(client, source, cat).status_code == 409
    manifest["version"] = "2.0.0"
    remote["revision"] = "d" * 40
    remote["files"]["skills/fixture/resource.yaml"] = json.dumps(manifest).encode()
    cat = client.post(f"/v1/resource-sources/{source}/refresh").json()
    conflict = apply(client, source, cat)
    assert conflict.status_code == 409
    success = apply(client, source, cat, names={cat["items"][0]["key"]: "fixture-v2"})
    assert success.status_code == 200, success.text
    assert len(client.get("/v1/integrations/skills").json()["items"]) == 2
    bad = apply(client, source, cat, digest="0" * 64)
    assert bad.status_code == 409


def test_plain_skill_directory_links_and_multiple_skills(resources):
    client, _, remote, _ = resources
    remote["files"].pop("source.json")
    remote["files"].pop("skills/fixture/resource.yaml")
    remote["files"]["other/SKILL.md"] = b"---\nname: second\ndescription: Another\n---\n"
    source = add(client)
    assert len(catalog(client, source)["items"]) == 2
    sub = add(client, "https://github.com/test/resources/blob/main/skills/fixture/SKILL.md")
    cat = catalog(client, sub)
    assert len(cat["items"]) == 1
    assert apply(client, sub, cat).status_code == 200
    assert (
        parse_url("https://github.com/test/resources/tree/feature/test/skills", "feature/test")[
            "path"
        ]
        == "skills"
    )


@pytest.mark.parametrize("failure", ["bad_blob", "truncated", "rate_limit"])
def test_failed_refresh_never_replaces_last_snapshot(resources, failure):
    client, _, remote, _ = resources
    source = add(client)
    remote["revision"] = "c" * 40
    remote[failure] = True
    # Force a new manifest blob to avoid using the verified cache.
    remote["files"]["source.json"] += b" "
    refreshed = client.post(f"/v1/resource-sources/{source}/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["revision"] == "a" * 40
    assert refreshed.json()["refresh_error"]


def test_symlink_and_undeclared_file_are_rejected(resources):
    client, _, remote, _ = resources
    remote["modes"]["skills/fixture/SKILL.md"] = "120000"
    assert (
        client.post(
            "/v1/resource-sources", json={"url": "https://github.com/test/resources"}
        ).status_code
        == 422
    )
    remote["modes"] = {}
    source = add(client)
    cat = catalog(client, source)
    key = cat["items"][0]["key"]
    path = f"/v1/resource-sources/{source}/items/{key}/file"
    assert (
        client.get(path, params={"revision": cat["revision"], "path": "../private"}).status_code
        == 404
    )


def test_recipes_strip_secrets_and_never_enable_or_execute(resources):
    client, _, remote, _ = resources
    for kind, config in [
        (
            "tool_api",
            {
                "method": "GET",
                "url": "https://example.com/api",
                "secret": "do-not-import",
                "auth_kind": "bearer",
            },
        ),
        (
            "mcp",
            {
                "transport": "stdio",
                "command": "fixture-mcp",
                "environment": {"TOKEN": "do-not-import"},
            },
        ),
    ]:
        manifest = {
            "id": kind.replace("_", "-"),
            "name": kind,
            "type": "integration",
            "version": "1.0.0",
            "description": "Recipe",
            "license": "MIT",
            "status": "published",
            "format": "fpl-integration-v1",
            "entry": "recipe.json",
            "files": ["recipe.json"],
        }
        remote["files"][kind + "/resource.yaml"] = json.dumps(manifest).encode()
        remote["files"][kind + "/recipe.json"] = json.dumps(
            {"kind": kind, "config": config}
        ).encode()
    source = add(client)
    cat = catalog(client, source)
    for item in cat["items"]:
        response = apply(client, source, cat, key=item["key"])
        assert response.status_code == 200, response.text
    api = client.get("/v1/integrations/tool-apis").json()["items"][0]
    mcp = client.get("/v1/integrations/mcp").json()["items"][0]
    assert not api["enabled"] and not api["has_secret"] and not mcp["enabled"]


def test_missing_dependency_prevents_partial_install(resources):
    client, _, remote, manifest = resources
    manifest["dependencies"] = [{"id": "missing", "version": "1.0.0"}]
    remote["files"]["skills/fixture/resource.yaml"] = json.dumps(manifest).encode()
    source = add(client)
    result = apply(client, source, catalog(client, source))
    assert result.status_code == 422
    assert not client.get("/v1/integrations/skills").json()["items"]
    assert not client.get("/v1/resource-library/imports").json()["items"]


def test_account_boundaries_and_csrf(resources):
    client, app, _remote, _ = resources
    source = add(client)
    cat = catalog(client, source)
    assert apply(client, source, cat).status_code == 200
    original_cookie = client.cookies["core_session"]
    original_csrf = client.headers["x-core-csrf"]
    user = app.state.accounts.create("reader", "Reader", "fixture-password-123")
    token, csrf = app.state.accounts.issue(user["id"])
    client.cookies.clear()
    client.cookies.set("core_session", token)
    client.headers["x-core-csrf"] = csrf
    assert client.get("/v1/resource-library/imports").json()["items"] == []
    assert client.post("/v1/resource-library/imports", json={}).status_code == 422
    client.cookies.clear()
    client.cookies.set("core_session", original_cookie)
    client.headers["x-core-csrf"] = "wrong"
    assert client.post(f"/v1/resource-sources/{source}/refresh").status_code == 403
    client.headers["x-core-csrf"] = original_csrf


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b",
        "https://github.com.evil/a/b",
        "https://github.com/a/b/tree/main/../secret",
        "https://user:pass@github.com/a/b",
    ],
)
def test_rejects_non_github_or_unsafe_links(url):
    with pytest.raises(ValueError):
        parse_url(url)


def test_default_source_loads_without_add_or_website_account(resources, monkeypatch):
    from dsh_core_worker.resource_library.library import DEFAULT_SOURCE

    client, _app, _remote, _ = resources
    monkeypatch.setitem(DEFAULT_SOURCE, "url", "https://github.com/test/resources")
    cat = catalog(client, "official")
    assert cat["items"]
    first = apply(client, "official", cat).json()
    assert client.delete("/v1/resource-sources/official").status_code == 200
    assert add(client) == "official"
    assert apply(client, "official", catalog(client, "official")).json() == first


def test_declared_previews_are_bound_to_content_and_files_are_scoped(resources):
    client, _app, remote, manifest = resources
    manifest["previews"] = [{"path": "preview.png", "kind": "image", "label": "Preview"}]
    manifest["preview_content_sha256"] = "0" * 64
    remote["files"]["skills/fixture/preview.png"] = b"fixture-image"
    remote["files"]["skills/fixture/resource.yaml"] = json.dumps(manifest).encode()
    source = add(client)
    cat = catalog(client, source)
    key = cat["items"][0]["key"]
    detail = client.get(
        f"/v1/resource-sources/{source}/items/{key}", params={"revision": cat["revision"]}
    ).json()
    assert detail["preview_status"] == "stale"
    response = client.get(
        f"/v1/resource-sources/{source}/items/{key}/file",
        params={"revision": cat["revision"], "path": "preview.png"},
    )
    assert (
        response.content == b"fixture-image"
        and response.headers["content-security-policy"] == "sandbox"
    )


def test_dependencies_are_installed_together_and_rollback_cleans_files(resources, monkeypatch):
    client, app, remote, manifest = resources
    dependency = {**manifest, "id": "dependency", "files": ["SKILL.md"], "name": "Dependency"}
    manifest["dependencies"] = [{"id": "dependency", "version": "1.0.0"}]
    remote["files"]["skills/fixture/resource.yaml"] = json.dumps(manifest).encode()
    remote["files"]["skills/dependency/resource.yaml"] = json.dumps(dependency).encode()
    remote["files"]["skills/dependency/SKILL.md"] = (
        b"---\nname: fixture-dependency\ndescription: Dependency\n---\n"
    )
    source = add(client)
    cat = catalog(client, source)
    root = next(i for i in cat["items"] if i["id"] == "fixture")
    from dsh_core_worker.integrations.contracts.errors import IntegrationValidationError
    from dsh_core_worker.integrations.skills.catalog import SkillCatalog

    original = SkillCatalog.create
    count = 0

    async def fail_second(self, *args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise IntegrationValidationError("fixture failure")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(SkillCatalog, "create", fail_second)
    assert apply(client, source, cat, root["key"]).status_code == 422
    assert not client.get("/v1/integrations/skills").json()["items"]
    assert not client.get("/v1/resource-library/imports").json()["items"]
    assert not list(app.state.integrations.root.glob("**/SKILL.md"))
    monkeypatch.setattr(SkillCatalog, "create", original)
    result = apply(client, source, cat, root["key"])
    assert result.status_code == 200, result.text
    assert len(result.json()["resources"]) == 2


def test_removed_source_keeps_installed_copies_and_receipts(resources):
    client, _app, _remote, _ = resources
    source = add(client)
    cat = catalog(client, source)
    assert apply(client, source, cat).status_code == 200
    assert client.delete(f"/v1/resource-sources/{source}").status_code == 200
    assert client.get("/v1/resource-library/imports").json()["items"]
    assert client.get("/v1/integrations/skills").json()["items"]


def test_concurrent_double_click_returns_one_receipt(resources):
    import concurrent.futures

    client, _app, _remote, _ = resources
    source = add(client)
    cat = catalog(client, source)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: apply(client, source, cat), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert len(client.get("/v1/resource-library/imports").json()["items"]) == 1


def test_installed_resource_remains_usable_after_library_recreation(resources):
    from dsh_core_worker.resource_library.library import ResourceLibrary

    client, app, remote, _ = resources
    source = add(client)
    cat = catalog(client, source)
    first = apply(client, source, cat).json()
    app.state.resource_library = ResourceLibrary(app.state.integrations)
    remote["offline"] = True
    assert catalog(client, source)["items"][0]["installed"]
    assert apply(client, source, cat).json() == first


@pytest.mark.parametrize(
    "change",
    [
        {"origin": 42},
        {"version": "main"},
        {"id": "../unsafe"},
        {"previews": [{"path": "SKILL.md", "kind": "image", "label": {}}]},
    ],
)
def test_malformed_resource_metadata_returns_validation_error(resources, change):
    client, _app, remote, manifest = resources
    manifest.update(change)
    remote["files"]["skills/fixture/resource.yaml"] = json.dumps(manifest).encode()
    response = client.post(
        "/v1/resource-sources", json={"url": "https://github.com/test/resources"}
    )
    assert response.status_code == 422, response.text


def test_malformed_source_descriptor_does_not_break_cached_browsing(resources):
    client, _app, remote, _ = resources
    source = add(client)
    remote["revision"] = "c" * 40
    remote["files"]["source.json"] = b"[]"
    result = client.post(f"/v1/resource-sources/{source}/refresh")
    assert result.status_code == 200 and result.json()["refresh_error"]
    assert result.json()["revision"] == "a" * 40
