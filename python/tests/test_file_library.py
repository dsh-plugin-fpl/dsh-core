import base64
import io
import zipfile
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

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


def folder(client, name, parent=None):
    response = client.post("/v1/file-library/folders", json={"name": name, "parent_id": parent})
    assert response.status_code == 200, response.text
    return response.json()


def resource(client, name="note.txt", data=b"file body", mime="text/plain"):
    response = client.post(
        "/v1/rpc",
        json={
            "method": "resource.put",
            "params": {
                "name": name,
                "mime_type": mime,
                "data_base64": base64.b64encode(data).decode(),
            },
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def zipped(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for path, value in entries:
            archive.writestr(path, value)
    return stream.getvalue()


def test_folder_editing_cycles_duplicate_names_and_empty_delete(service):
    _, client = service
    parent = folder(client, "制作方案")
    child = folder(client, "人设", parent["id"])
    url = "/v1/file-library/folders/" + parent["id"]
    assert (
        client.patch(url, json={"expected_revision": 1, "parent_id": child["id"]}).status_code
        == 422
    )
    assert client.delete(url, params={"expected_revision": 1}).status_code == 422
    response = client.patch(url, json={"expected_revision": 1, "name": "新方案"})
    assert response.status_code == 200 and response.json()["revision"] == 2
    assert client.patch(url, json={"expected_revision": 1, "name": "过期修改"}).status_code == 409
    assert client.post("/v1/file-library/folders", json={"name": "新方案"}).status_code == 409
    for invalid in [" ", "../outside", "a\\b", "..", "\u0000"]:
        assert client.post("/v1/file-library/folders", json={"name": invalid}).status_code == 422
    assert (
        client.patch(
            "/v1/file-library/folders/" + child["id"],
            json={"expected_revision": 1, "parent_id": None},
        ).status_code
        == 200
    )
    assert client.delete(url, params={"expected_revision": 2}).status_code == 200
    assert client.get("/v1/file-library", params={"folder_id": parent["id"]}).status_code == 404


def test_entry_edits_search_pagination_filter_and_original_retention(service):
    _, client = service
    target = folder(client, "人设")
    item = resource(client)
    url = "/v1/file-library/entries/" + item["id"]
    response = client.patch(
        url, json={"expected_revision": 1, "folder_id": target["id"], "name": "新文件.md"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2
    assert not client.get("/v1/file-library").json()["items"]
    page = client.get("/v1/file-library", params={"folder_id": target["id"]}).json()
    assert page["items"][0]["name"] == "新文件.md" and page["file_count"] == 1
    assert page["breadcrumbs"][0]["name"] == "人设"
    assert client.get("/v1/file-library", params={"search": "新文件"}).json()["file_count"] == 1
    assert (
        client.get("/v1/file-library", params={"search": "人设"}).json()["folders"][0]["id"]
        == target["id"]
    )
    assert (
        client.delete(
            "/v1/file-library/folders/" + target["id"], params={"expected_revision": 1}
        ).status_code
        == 422
    )
    assert client.patch(url, json={"expected_revision": 1, "name": "lost"}).status_code == 409
    assert client.delete(url, params={"expected_revision": 1}).status_code == 409
    download = client.get(url + "/content", params={"download": True})
    assert (
        download.status_code == 200
        and "%E6%96%B0%E6%96%87%E4%BB%B6.md" in download.headers["content-disposition"]
    )
    assert client.delete(url, params={"expected_revision": 2}).status_code == 200
    assert not client.get("/v1/file-library", params={"search": "新文件"}).json()["items"]
    assert client.get(url + "/content").status_code == 404
    assert client.get(f"/v1/resources/{item['id']}/content").content == b"file body"
    assert (
        client.delete(
            "/v1/file-library/folders/" + target["id"], params={"expected_revision": 1}
        ).status_code
        == 200
    )
    for name in ["b.txt", "a.txt", "c.txt"]:
        resource(client, name)
    page = client.get("/v1/file-library", params={"sort": "name", "limit": 2}).json()
    assert [item["name"] for item in page["items"]] == ["a.txt", "b.txt"]
    next_page = client.get(
        "/v1/file-library", params={"sort": "name", "limit": 2, "after": page["next_cursor"]}
    ).json()
    assert [item["name"] for item in next_page["items"]] == ["c.txt"]
    assert next_page["file_count"] == 3
    assert not client.get("/v1/file-library", params={"kind": "image"}).json()["items"]
    assert client.get("/v1/file-library", params={"after": "missing"}).status_code == 409


def test_library_isolation_read_write_and_export_for_admin_and_other_user(service):
    _, client = service
    owned = folder(client, "private")
    item = resource(client)
    client.post(
        "/v1/identity/accounts",
        json={"username": "beta", "display_name": "Beta", "password": "isolated-password-123"},
    )
    owner_cookies = dict(client.cookies)
    owner_csrf = client.headers["X-Core-CSRF"]
    client.cookies.clear()
    result = client.post(
        "/v1/identity/login", json={"username": "beta", "password": "isolated-password-123"}
    )
    client.headers["X-Core-CSRF"] = result.json()["csrf"]
    assert client.get("/v1/file-library").json()["file_count"] == 0
    assert client.get("/v1/file-library/folders").json() == []
    assert client.get("/v1/file-library", params={"folder_id": owned["id"]}).status_code == 404
    assert (
        client.patch(
            "/v1/file-library/entries/" + item["id"],
            json={"expected_revision": 1, "name": "stolen"},
        ).status_code
        == 404
    )
    assert (
        client.get("/v1/file-library/folder-export", params={"folder_id": owned["id"]}).status_code
        == 404
    )
    other = folder(client, "other")
    client.cookies.clear()
    client.cookies.update(owner_cookies)
    client.headers["X-Core-CSRF"] = owner_csrf
    assert (
        client.delete(
            "/v1/file-library/folders/" + other["id"], params={"expected_revision": 1}
        ).status_code
        == 404
    )
    csrf = client.headers.pop("X-Core-CSRF")
    assert client.post("/v1/file-library/folders", json={"name": "no csrf"}).status_code == 403
    client.headers["X-Core-CSRF"] = csrf


def test_zip_import_export_idempotency_and_bad_archive_preflight(service):
    _, client = service
    body = zipped([("制作方案/", b""), ("制作方案/a.txt", b"hello"), ("制作方案/empty/", b"")])
    request = {"request_id": str(uuid4())}
    first = client.post(
        "/v1/file-library/archive-imports",
        data=request,
        files={"archive": ("a.zip", body, "application/zip")},
    )
    assert first.status_code == 200, first.text
    assert first.json()["files"] == 1
    repeat = client.post(
        "/v1/file-library/archive-imports",
        data=request,
        files={"archive": ("a.zip", body, "application/zip")},
    )
    assert repeat.json() == first.json()
    changed = client.post(
        "/v1/file-library/archive-imports",
        data=request,
        files={"archive": ("b.zip", zipped([("b.txt", b"different")]), "application/zip")},
    )
    assert changed.status_code == 409
    root = client.get("/v1/file-library").json()
    assert root["folder_count"] == 1
    response = client.get("/v1/file-library/folder-export")
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("制作方案/a.txt") == b"hello"
        assert "制作方案/empty/" in archive.namelist()
    nested = client.get(
        "/v1/file-library/folder-export", params={"folder_id": root["folders"][0]["id"]}
    )
    with zipfile.ZipFile(io.BytesIO(nested.content)) as archive:
        assert archive.read("a.txt") == b"hello"
    for entries in [
        [("../escape.txt", b"bad")],
        [("/absolute", b"bad")],
        [("same", b"a"), ("same", b"b")],
        [("a", b"file"), ("a/b", b"child")],
    ]:
        result = client.post(
            "/v1/file-library/archive-imports",
            data={"request_id": str(uuid4())},
            files={"archive": ("bad.zip", zipped(entries), "application/zip")},
        )
        assert result.status_code == 422, result.text
    assert client.get("/v1/file-library").json()["folder_count"] == 1
    request = {"request_id": str(uuid4()), "paths": ["local/a.txt", "local/sub/b.txt"]}
    files = [("files", ("a.txt", b"a", "text/plain")), ("files", ("b.txt", b"b", "text/plain"))]
    response = client.post("/v1/file-library/directory-imports", data=request, files=files)
    assert response.status_code == 200 and response.json()["files"] == 2, response.text
    assert (
        client.post("/v1/file-library/directory-imports", data=request, files=files).json()
        == response.json()
    )


def test_import_recovers_same_receipt_without_duplicates_and_preflights_limits(
    service, monkeypatch
):
    from dsh_core_worker import file_library_transfers as transfers
    from dsh_core_worker.contracts import CoreError

    _, client = service
    data = zipped([("recovery/a.txt", b"first"), ("recovery/b.txt", b"second")])
    request_id = str(uuid4())
    original = transfers.LibraryImport.file
    interrupted = False

    def interrupt_once(self, path, data, mime=None):
        nonlocal interrupted
        original(self, path, data, mime)
        if not interrupted:
            interrupted = True
            raise CoreError("simulated interruption")

    monkeypatch.setattr(transfers.LibraryImport, "file", interrupt_once)
    first = client.post(
        "/v1/file-library/archive-imports",
        data={"request_id": request_id},
        files={"archive": ("a.zip", data, "application/zip")},
    )
    assert first.status_code == 422
    assert client.get("/v1/file-library/imports/" + request_id).json()["status"] == "running"
    resumed = client.post(
        "/v1/file-library/archive-imports",
        data={"request_id": request_id},
        files={"archive": ("a.zip", data, "application/zip")},
    )
    assert resumed.status_code == 200 and resumed.json()["files"] == 2
    assert client.get("/v1/file-library/imports/" + request_id).json()["result"] == resumed.json()
    assert client.get("/v1/file-library", params={"search": ".txt"}).json()["file_count"] == 2
    monkeypatch.setattr(transfers, "MAX_BYTES", 8)
    rejected = client.post(
        "/v1/file-library/archive-imports",
        data={"request_id": str(uuid4())},
        files={
            "archive": ("large.zip", zipped([("unwritten/a.txt", b"x" * 9)]), "application/zip")
        },
    )
    assert rejected.status_code == 422
    assert client.get("/v1/file-library").json()["folder_count"] == 1
    monkeypatch.setattr(transfers, "MAX_BYTES", 256 * 1024 * 1024)
    monkeypatch.setattr(transfers, "MAX_ENTRIES", 1)
    rejected = client.post(
        "/v1/file-library/directory-imports",
        data={"request_id": str(uuid4()), "paths": ["too/deep/a.txt"]},
        files=[("files", ("a.txt", b"a", "text/plain"))],
    )
    assert rejected.status_code == 422
    assert client.get("/v1/file-library").json()["folder_count"] == 1


def test_directory_import_accepts_large_multipart_before_domain_preflight(service):
    _, client = service
    paths = [f"batch/{i}.txt" for i in range(1000)] + ["../invalid.txt"]
    response = client.post(
        "/v1/file-library/directory-imports",
        data={"request_id": str(uuid4()), "paths": paths},
        files=[("files", (f"{i}.txt", b"x", "text/plain")) for i in range(1001)],
    )
    assert response.status_code == 422 and "路径" in response.json()["detail"]
    assert client.get("/v1/file-library").json()["folder_count"] == 0
