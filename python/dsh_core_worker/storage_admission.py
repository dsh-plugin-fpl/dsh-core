"""A durable offline-maintenance marker shared by HTTP and execution admission."""

from pathlib import Path


def paused(root):
    root = Path(root)
    for candidate in [root, *list(root.parents)[:4]]:
        if (candidate / ".storage-paused").exists():
            return True
    return False
