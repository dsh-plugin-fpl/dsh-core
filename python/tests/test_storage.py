import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from dsh_core_worker.private_files import atomic_private
from dsh_core_worker.service import create_app
from dsh_core_worker.storage_io import copy_file, copy_tree, digest, validate_target
from dsh_core_worker.storage_location import new_application_root, read_location, root_for
from dsh_core_worker.storage_manager import StorageManager


def configure(tmp_path, monkeypatch):
    locator = tmp_path / "preferences.json"
    monkeypatch.setenv("DSH_FPL_STORAGE_CONFIG", str(locator))
    root = tmp_path / "应用 数据"
    root.mkdir()
    atomic_private(root / ".dsh-fpl-storage.json", b'{"instance":"owned"}')
    atomic_private(
        locator,
        json.dumps({"version": 1, "revision": 1, "instance": "owned", "root": str(root)}).encode(),
    )
    return locator, root


def test_locator_keeps_explicit_isolation_and_never_falls_back_on_missing_volume(
    tmp_path, monkeypatch
):
    _locator, root = configure(tmp_path, monkeypatch)
    (root / "core").mkdir()
    assert root_for("core", tmp_path / "legacy") == root / "core"
    assert root_for("core", tmp_path / "legacy", tmp_path / "isolated") == tmp_path / "isolated"
    assert new_application_root("dsh-thought-workshop") == root / "workshops/dsh-thought-workshop"
    root.rename(tmp_path / "removed-volume")
    with pytest.raises(ValueError, match="不可访问"):
        root_for("core", tmp_path / "legacy")
    assert not root.exists()


def test_locator_rejects_replaced_instance_and_public_permissions(tmp_path, monkeypatch):
    locator, root = configure(tmp_path, monkeypatch)
    atomic_private(root / ".dsh-fpl-storage.json", b'{"instance":"other"}')
    with pytest.raises(ValueError, match="实例标识"):
        read_location()
    locator.chmod(0o644)
    with pytest.raises(ValueError, match="当前用户"):
        read_location()


def test_target_validation_preserves_unrelated_content_and_rejects_overlap(migration_root):
    tmp_path = migration_root
    source = tmp_path / "source"
    source.mkdir()
    for target in [source, source / "nested", tmp_path]:
        with pytest.raises(ValueError, match="重叠"):
            validate_target(target, [source])
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "keep.txt").write_text("unchanged")
    with pytest.raises(ValueError, match="非空"):
        validate_target(target, [source])
    assert (target / "keep.txt").read_text() == "unchanged"


def test_snapshot_includes_wal_records_and_resumes_without_overwriting(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    database = sqlite3.connect(source / "service.db")
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("CREATE TABLE example(value TEXT)")
    database.execute("INSERT INTO example VALUES('preserved')")
    database.commit()
    (source / "original.bin").write_bytes(bytes(range(256)) * 8192)
    receipt = tmp_path / "receipt.json"
    first = copy_tree(source, target, receipt)
    second = copy_tree(source, target, receipt)
    assert first == second
    with sqlite3.connect(target / "service.db") as copied:
        assert copied.execute("SELECT value FROM example").fetchone()[0] == "preserved"
    assert digest(source / "original.bin") == digest(target / "original.bin")
    (target / "original.bin").write_bytes(b"changed")
    with pytest.raises(ValueError, match="回执不一致"):
        copy_tree(source, target, receipt)
    database.close()


def test_file_copy_does_not_follow_symlinks(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"private")
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(ValueError):
        copy_file(link, target)
    assert not target.exists()


def test_storage_api_requires_machine_owner_origin_and_csrf(migration_root, monkeypatch):
    tmp_path = migration_root
    monkeypatch.setenv("DSH_FPL_STORAGE_CONFIG", str(tmp_path / "no-locator"))
    original = StorageManager.__init__

    def isolated(self, repository, current_root):
        original(self, repository, current_root)
        self.control = tmp_path / "control"

    monkeypatch.setattr(StorageManager, "__init__", isolated)
    app = create_app(tmp_path / "core", origin="http://testserver")
    with TestClient(app) as client:
        assert client.get("/v1/storage").status_code == 401
        signed = client.post(
            "/v1/session",
            json={"ticket": app.state.access.ticket()},
            headers={"Origin": "http://testserver"},
        )
        client.headers.update({"Origin": "http://testserver", "X-Core-CSRF": signed.json()["csrf"]})
        assert client.get("/v1/storage").json()["revision"] == 0
        payload = {"target_root": str(tmp_path / "target"), "expected_revision": 0}
        assert (
            client.post(
                "/v1/storage/plans", json=payload, headers={"Origin": "http://evil.test"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/v1/storage/plans", json=payload, headers={"X-Core-CSRF": "wrong"}
            ).status_code
            == 403
        )
        planned = client.post("/v1/storage/plans", json=payload)
        assert planned.status_code == 200, planned.text
        assert not (tmp_path / "target").exists()
        other = app.state.accounts.create(
            "other-admin", "Other", "isolated-password-123", role="admin"
        )
        token, csrf = app.state.accounts.issue(other["id"])
        client.cookies.clear()
        client.cookies.set("core_session", token)
        client.headers["X-Core-CSRF"] = csrf
        assert client.get("/v1/storage").status_code == 403
        assert "/v1/storage/plans" in app.openapi()["paths"]


def test_relocation_rebuilds_only_managed_model_environments_and_caches(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    for relative in ["state/local-models/environments/v1", "state/local-models/downloads/uv-cache"]:
        directory = source / relative
        directory.mkdir(parents=True)
        (directory / "generated-link").symlink_to(tmp_path / "missing")
    weights = source / "state/local-models/models/model/weights.bin"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"verified weights")
    assert copy_tree(source, target, tmp_path / "receipt.json")["files"] == 1
    assert (target / weights.relative_to(source)).read_bytes() == b"verified weights"
    assert not (target / "state/local-models/environments").exists()
    assert not (target / "state/local-models/downloads/uv-cache").exists()


def test_formal_storage_rejects_local_intermediates(tmp_path):
    target = tmp_path / ".local" / "storage"
    with pytest.raises(ValueError, match="临时目录"):
        validate_target(target, [])
    assert not target.exists()
