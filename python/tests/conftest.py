"""Isolated fixtures for production storage path-policy tests."""
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


@pytest.fixture
def migration_root():
    # Real storage rejects .local by design. Use a library-managed, short-lived
    # system directory for these path-policy tests; no real locator is read.
    with TemporaryDirectory(prefix="dsh-fpl-storage-test-") as directory:
        yield Path(directory).resolve()
