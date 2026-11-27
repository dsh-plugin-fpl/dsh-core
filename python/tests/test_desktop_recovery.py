"""Offline recovery publication must not lose either the previous or new tree."""

import fcntl
import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from dsh_core_worker.store import Store
from fpl_core.migration import activate_product, restore_models


def trees(tmp_path):
    staging = tmp_path / "staging"
    target = tmp_path / "live"
    backup = tmp_path / "previous"
    for p in [staging, target]:
        p.mkdir()
        (p / "identity.json").write_text(json.dumps({"id": "dsh-visual-workshop"}))
    (staging / ".desktop-migration.json").write_text("{}")
    (staging / "project").write_bytes(b"restored original project")
    (target / "preferences").write_bytes(b"new desktop preferences")
    return staging, target, backup


def test_cutover_preserves_previous_desktop(tmp_path):
    staging, target, backup = trees(tmp_path)
    result = activate_product(staging, target, backup, product_lock=nullcontext())
    assert result["activated"]
    assert (target / "project").read_bytes() == b"restored original project"
    assert (backup / "preferences").read_bytes() == b"new desktop preferences"
    assert not staging.exists()


def test_failed_publication_restores_previous_desktop(tmp_path, monkeypatch):
    staging, target, backup = trees(tmp_path)
    original = Path.rename

    def fail(source, destination):
        if source == staging:
            raise OSError("simulated publication interruption")
        return original(source, destination)

    monkeypatch.setattr(Path, "rename", fail)
    with pytest.raises(OSError):
        activate_product(staging, target, backup, product_lock=nullcontext())
    assert (target / "preferences").read_bytes() == b"new desktop preferences"
    assert (staging / "project").read_bytes() == b"restored original project"
    assert not backup.exists()


def test_active_core_blocks_cutover(tmp_path):
    staging, target, backup = trees(tmp_path)
    state = target / "embedded-core/state"
    state.mkdir(parents=True)
    with (state / "worker.lock").open("wb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            activate_product(staging, target, backup, product_lock=nullcontext())
    assert (target / "preferences").is_file() and not backup.exists()


def test_restore_without_enabled_models_needs_no_download(tmp_path):
    store = Store(tmp_path / "state")
    store.close()
    report = restore_models(tmp_path)
    assert report == {"restored": True, "accounts": [{"enabled": 0, "installed": 0}]}
    assert not (tmp_path / "state/local-models/environments").exists()
