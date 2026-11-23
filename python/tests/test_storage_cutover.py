import json
from types import SimpleNamespace

import pytest

from dsh_core_worker.storage_manager import StorageManager
from dsh_core_worker.storage_worker import run
from dsh_core_worker.store import Store


@pytest.mark.parametrize("fail", [False, True])
def test_cutover_commits_only_after_health_or_restores_original(migration_root, monkeypatch, fail):
    tmp_path = migration_root
    repository, source, target = tmp_path / "install", tmp_path / "old", tmp_path / "new"
    locator = tmp_path / "locator.json"
    monkeypatch.setenv("DSH_FPL_STORAGE_CONFIG", str(locator))
    store = Store(source / "state")
    store.save_config("keep", {"text": "existing"}, 0)
    store.close()
    manager = StorageManager(repository, source)
    plan = manager.plan(str(target), 0)
    manager.save(
        "jobs/" + plan["id"] + ".json", {"id": plan["id"], "status": "queued", "created_at": 0}
    )
    calls = []

    def lifecycle(args, **_):
        calls.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("dsh_core_worker.storage_worker.subprocess.run", lifecycle)

    class Client:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, _):
            if fail:
                raise ValueError("isolated health failed")
            return SimpleNamespace(raise_for_status=lambda: None, json=dict)

    monkeypatch.setattr("dsh_core_worker.storage_worker.httpx.Client", Client)
    # Do not fake APFS clone subprocess copying; small fixtures use real bounded copies.
    monkeypatch.setattr(
        "dsh_core_worker.storage_io.os.uname", lambda: SimpleNamespace(sysname="test")
    )
    if fail:
        with pytest.raises(ValueError, match="health failed"):
            run(repository, source, plan["id"])
        assert not locator.exists()
        assert manager.job(plan["id"])["status"] == "rolled_back"
    else:
        run(repository, source, plan["id"])
        assert json.loads(locator.read_text())["root"] == str(target)
        assert manager.job(plan["id"])["status"] == "completed"
        assert not (target / "core/.storage-paused").exists()
    assert not (source / ".storage-paused").exists()
    original = Store(source / "state")
    assert original.config("keep")["value"] == {"text": "existing"}
    original.close()


def test_new_relocations_do_not_implicitly_import_legacy(migration_root, monkeypatch):
    tmp_path = migration_root
    monkeypatch.setenv("DSH_FPL_STORAGE_CONFIG", str(tmp_path / "locator"))
    source = tmp_path / "old"
    source.mkdir()
    manager = StorageManager(tmp_path / "install", source)
    manager.save("legacy-source.json", {"bytes": 1234})
    assert manager.plan(str(tmp_path / "new"), 0)["legacy"] is None
    assert manager.plan(str(tmp_path / "with-old"), 0, True)["legacy"]["bytes"] == 1234
