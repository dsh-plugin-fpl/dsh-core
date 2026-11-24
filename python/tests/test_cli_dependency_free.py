"""Stopping and inspecting a service must not require its damaged environment."""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path


def test_lifecycle_status_and_stop_work_without_site_packages(tmp_path):
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        port = socket_.getsockname()[1]
    package_root = Path(__file__).resolve().parents[1]
    for action in ["status", "stop"]:
        result = subprocess.run([sys.executable, "-S", "-m", "dsh_core_worker.cli", action,
            "--root", str(tmp_path / "isolated-core"), "--port", str(port), "--no-open"],
            env={**os.environ, "PYTHONPATH": str(package_root)}, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        if action == "status":
            assert json.loads(result.stdout)["running"] is False
