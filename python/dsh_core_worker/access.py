"""Local owner sessions, revocable bridge pairing and encrypted provider secrets."""

import hashlib
import json
import secrets
import time
from pathlib import Path

from cryptography.fernet import Fernet

from .contracts import CoreError
from .private_files import atomic_private


class Access:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = self.root / "secret.key"
        if not key.exists():
            atomic_private(key, Fernet.generate_key())
        self.cipher = Fernet(key.read_bytes())
        self.sessions = {}

    def read(self, name, default=None):
        path = self.root / name
        if not path.exists():
            return {} if default is None else default
        return json.loads(self.cipher.decrypt(path.read_bytes()))

    def write(self, name, value):
        atomic_private(self.root / name, self.cipher.encrypt(json.dumps(value).encode()))

    def ticket(self):
        value = secrets.token_urlsafe(32)
        self.write("launch.enc", {"digest": self.digest(value), "expires": time.time() + 120})
        return value

    def exchange(self, value):
        ticket = self.read("launch.enc")
        if ticket.get("expires", 0) < time.time() or not secrets.compare_digest(
            ticket.get("digest", ""), self.digest(value)
        ):
            raise CoreError("启动链接已失效，请重新打开看板", "unauthorized")
        self.write("launch.enc", {})
        session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.sessions[self.digest(session)] = {"csrf": csrf, "expires": time.time() + 86400}
        return session, csrf

    def session(self, value):
        result = self.sessions.get(self.digest(value or ""))
        return result if result and result["expires"] > time.time() else None

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def pair(self, name):
        identity, token = secrets.token_hex(8), secrets.token_urlsafe(40)
        values = self.read("bridges.enc")
        values[identity] = {"id": identity, "name": str(name)[:120], "digest": self.digest(token)}
        self.write("bridges.enc", values)
        return {"id": identity, "name": values[identity]["name"], "token": token}

    def bridges(self):
        return [{"id": v["id"], "name": v["name"]} for v in self.read("bridges.enc").values()]

    def authenticate_bridge(self, token):
        for value in self.read("bridges.enc").values():
            if secrets.compare_digest(value["digest"], self.digest(token)):
                return value["id"]
        raise CoreError("连接凭据无效或已撤销", "unauthorized")

    def revoke(self, identity):
        values = self.read("bridges.enc")
        values.pop(identity, None)
        self.write("bridges.enc", values)
