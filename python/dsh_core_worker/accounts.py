"""Core-owned accounts, revocable sessions and audience-bound application login.

Only public account profiles leave this store. Tenant execution and encrypted
provider state remain in separate per-account runtimes.
"""

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .contracts import CoreError
from .identity_schema import Migration, apply, check

MIGRATIONS = (
    Migration(
        "identity-v1",
        """
CREATE TABLE account (
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 display_name TEXT NOT NULL, password TEXT, role TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 0,
 legacy INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX one_legacy_owner ON account(legacy) WHERE legacy=1;
CREATE TABLE account_session (
 digest TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES account(id),
 csrf TEXT NOT NULL, expires INTEGER NOT NULL, audience TEXT NOT NULL);
CREATE TABLE account_invite (digest TEXT PRIMARY KEY, expires INTEGER NOT NULL);
CREATE TABLE app_client (
 id TEXT PRIMARY KEY, digest TEXT NOT NULL, origin TEXT NOT NULL, callback TEXT NOT NULL);
CREATE TABLE app_login (
 digest TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES account(id),
 application_id TEXT NOT NULL REFERENCES app_client(id), expires INTEGER NOT NULL);
CREATE TABLE login_attempt (bucket TEXT PRIMARY KEY, failures INTEGER NOT NULL, expires INTEGER NOT NULL);
""",
    ),
)


MIGRATIONS += (
    Migration(
        "identity-applications-v2",
        "CREATE TABLE account_application (account_id TEXT NOT NULL REFERENCES account(id), application_id TEXT NOT NULL REFERENCES app_client(id), PRIMARY KEY(account_id,application_id));",
    ),
)

MIGRATIONS += (
    Migration(
        "identity-bridge-bindings-v3",
        "CREATE TABLE identity_bridge_binding (bridge_id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES account(id), application_id TEXT NOT NULL);",
    ),
)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(value, salt=None):
    if not isinstance(value, str) or not 12 <= len(value) <= 256:
        raise CoreError("密码长度需要 12–256 个字符", "validation")
    salt = salt or secrets.token_hex(16)
    encoded = hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ":" + encoded


def verify_password(value, encoded):
    try:
        salt, _ = encoded.split(":")
        return hmac.compare_digest(password_hash(value, salt), encoded)
    except (ValueError, TypeError, AttributeError, CoreError):
        # Comparable work for invalid/unknown accounts.
        hashlib.scrypt(b"invalid-password", salt=b"core-invalid", n=16384, r=8, p=1)
        return False


class Accounts:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "identity.db"
        # A new account store is explicitly bootstrapped with the service's owned
        # legacy identity; existing provider and application stores are untouched.
        apply(self.path, "core-identity", MIGRATIONS)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM account WHERE legacy=1").fetchone() is None:
                db.execute(
                    "INSERT INTO account(id,username,display_name,role,legacy) VALUES(?,?,?,?,1)",
                    (str(uuid4()), "local-owner", "本机所有者", "admin"),
                )
        self.initial = self.profiles(legacy=True)[0]

    @contextmanager
    def connection(self):
        check(self.path, "core-identity", MIGRATIONS)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def profile(row):
        return {
            **{k: row[k] for k in ("id", "username", "display_name", "role", "revision")},
            "active": bool(row["active"]),
            "legacy": bool(row["legacy"]),
        }

    def profiles(self, *, legacy=False):
        with self.connection() as db:
            return [
                self.profile(r)
                for r in db.execute(
                    "SELECT * FROM account"
                    + (" WHERE legacy=1" if legacy else "")
                    + " ORDER BY username"
                )
            ]

    def account(self, identity):
        with self.connection() as db:
            row = db.execute("SELECT * FROM account WHERE id=?", (identity,)).fetchone()
        if row is None or not row["active"]:
            raise CoreError("账号不可用", "unauthorized")
        return self.profile(row)

    def create(self, username, display_name, password, *, role="user", invite=None):
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9._-]{3,64}", username):
            raise CoreError("用户名需要 3–64 个字母、数字或 ._-", "validation")
        if (
            not isinstance(display_name, str)
            or not 1 <= len(display_name.strip()) <= 120
            or role not in {"admin", "user"}
        ):
            raise CoreError("账号信息无效", "validation")
        encoded = password_hash(password)
        identity = str(uuid4())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if invite is not None:
                row = db.execute(
                    "DELETE FROM account_invite WHERE digest=? AND expires>=? RETURNING digest",
                    (digest(invite), int(time.time())),
                ).fetchone()
                if row is None:
                    raise CoreError("邀请已失效", "unauthorized")
                if role != "user":
                    raise CoreError("邀请只能创建普通账号", "forbidden")
            try:
                db.execute(
                    "INSERT INTO account(id,username,display_name,password,role) VALUES(?,?,?,?,?)",
                    (identity, username.lower(), display_name.strip(), encoded, role),
                )
            except sqlite3.IntegrityError:
                raise CoreError("用户名已存在", "revision_conflict") from None
        return self.account(identity)

    def invitation(self):
        token = secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute(
                "INSERT INTO account_invite VALUES(?,?)", (digest(token), int(time.time()) + 86400)
            )
        return token

    def login(self, username, password, *, bucket="local"):
        key = digest(bucket)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            attempt = db.execute("SELECT * FROM login_attempt WHERE bucket=?", (key,)).fetchone()
            if attempt and attempt["expires"] > time.time() and attempt["failures"] >= 10:
                raise CoreError("登录尝试过多，请稍后重试", "forbidden")
            row = db.execute(
                "SELECT * FROM account WHERE username=? COLLATE NOCASE", (str(username)[:64],)
            ).fetchone()
            valid = verify_password(password, row["password"] if row else None)
            if not row or not row["active"] or not valid:
                db.execute(
                    "INSERT INTO login_attempt VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET failures=CASE WHEN expires<? THEN 1 ELSE failures+1 END, expires=excluded.expires",
                    (key, int(time.time()) + 300, int(time.time())),
                )
            else:
                db.execute("DELETE FROM login_attempt WHERE bucket=?", (key,))
        if not row or not row["active"] or not valid:
            raise CoreError("账号或密码无效", "unauthorized")
        return self.issue(row["id"])

    def issue(self, account_id, *, audience="core", ttl=86400):
        self.account(account_id)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute(
                "INSERT INTO account_session VALUES(?,?,?,?,?)",
                (digest(token), account_id, csrf, int(time.time()) + ttl, audience),
            )
        return token, csrf

    def session(self, token, *, audience="core"):
        if not isinstance(token, str) or len(token) > 256:
            return None
        with self.connection() as db:
            row = db.execute(
                "SELECT s.*,a.active,a.role,a.revision FROM account_session s JOIN account a ON a.id=s.account_id WHERE s.digest=? AND s.audience=? AND s.expires>? AND a.active=1",
                (digest(token), audience, int(time.time())),
            ).fetchone()
        return dict(row) if row else None

    def revoke(self, token):
        with self.connection() as db:
            db.execute("DELETE FROM account_session WHERE digest=?", (digest(token),))

    def update(self, identity, *, active, expected_revision):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM account WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise CoreError("账号不存在", "not_found")
            if row["legacy"]:
                raise CoreError("本机恢复账号不能禁用", "forbidden")
            changed = db.execute(
                "UPDATE account SET active=?,revision=revision+1 WHERE id=? AND revision=?",
                (int(active), identity, expected_revision),
            ).rowcount
            if not changed:
                raise CoreError("账号已更新", "revision_conflict")
            if not active:
                db.execute("DELETE FROM account_session WHERE account_id=?", (identity,))
        return {"updated": True}

    def register_client(self, identity, origin, callback):
        from urllib.parse import urlsplit

        parsed = urlsplit(origin)
        if (
            not re.fullmatch(r"[a-z0-9-]{3,80}", identity)
            or parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or not parsed.port
            or origin != f"http://127.0.0.1:{parsed.port}"
            or not callback.startswith("/apps/")
            or "?" in callback
            or "#" in callback
        ):
            raise CoreError("应用回调地址无效", "validation")
        token = secrets.token_urlsafe(40)
        with self.connection() as db:
            if db.execute("SELECT 1 FROM app_client WHERE id=?", (identity,)).fetchone():
                raise CoreError("应用已登记，请显式撤销后重新配对", "revision_conflict")
            db.execute(
                "INSERT INTO app_client VALUES(?,?,?,?)",
                (identity, digest(token), origin, callback),
            )
        return {"id": identity, "token": token, "origin": origin, "callback": callback}

    def client(self, identity, token=None):
        with self.connection() as db:
            row = db.execute("SELECT * FROM app_client WHERE id=?", (identity,)).fetchone()
        if not row or (token is not None and not hmac.compare_digest(row["digest"], digest(token))):
            raise CoreError("应用配对无效", "unauthorized")
        return {k: row[k] for k in ("id", "origin", "callback")}

    def code(self, account_id, application_id):
        self.account(account_id)
        client = self.client(application_id)
        token = secrets.token_urlsafe(32)
        self.grant(account_id, application_id)
        with self.connection() as db:
            db.execute(
                "INSERT INTO app_login VALUES(?,?,?,?)",
                (digest(token), account_id, application_id, int(time.time()) + 60),
            )
        return token, client

    def exchange(self, application_id, credential, code):
        self.client(application_id, credential)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "DELETE FROM app_login WHERE digest=? AND application_id=? AND expires>=? RETURNING account_id",
                (digest(code), application_id, int(time.time())),
            ).fetchone()
            if not row:
                raise CoreError("应用登录链接已失效", "unauthorized")
        token, csrf = self.issue(row["account_id"], audience=application_id)
        return {"token": token, "csrf": csrf, "account": self.account(row["account_id"])}

    def grant(self, account_id, application_id):
        with self.connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO account_application VALUES(?,?)",
                (account_id, application_id),
            )

    def has_grant(self, account_id, application_id):
        with self.connection() as db:
            return (
                db.execute(
                    "SELECT 1 FROM account_application WHERE account_id=? AND application_id=?",
                    (account_id, application_id),
                ).fetchone()
                is not None
            )

    def reset_password(self, account_id, password):
        encoded = password_hash(password)
        with self.connection() as db:
            row = db.execute(
                "UPDATE account SET password=?,revision=revision+1 WHERE id=?",
                (encoded, account_id),
            ).rowcount
            if not row:
                raise CoreError("账号不存在", "not_found")
            db.execute("DELETE FROM account_session WHERE account_id=?", (account_id,))

    def revoke_all(self, account_id):
        with self.connection() as db:
            db.execute("DELETE FROM account_session WHERE account_id=?", (account_id,))

    def applications(self, account_id):
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT c.id,c.origin,c.callback FROM app_client c JOIN account_application a ON c.id=a.application_id WHERE a.account_id=? ORDER BY c.id",
                    (account_id,),
                )
            ]

    def revoke_application(self, account_id, application_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM account_application WHERE account_id=? AND application_id=?",
                (account_id, application_id),
            )
            db.execute(
                "DELETE FROM account_session WHERE account_id=? AND audience=?",
                (account_id, application_id),
            )
            db.execute(
                "DELETE FROM app_login WHERE account_id=? AND application_id=?",
                (account_id, application_id),
            )

    def remove_client(self, application_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for table, column in [
                ("account_application", "application_id"),
                ("account_session", "audience"),
                ("app_login", "application_id"),
            ]:
                db.execute(f"DELETE FROM {table} WHERE {column}=?", (application_id,))
            db.execute("DELETE FROM app_client WHERE id=?", (application_id,))

    def bind_bridge(self, account_id, application_id, bridge_id):
        self.account(account_id)
        self.client(application_id)
        with self.connection() as db:
            db.execute(
                "INSERT INTO identity_bridge_binding VALUES(?,?,?) ON CONFLICT(bridge_id) DO UPDATE SET account_id=excluded.account_id,application_id=excluded.application_id",
                (bridge_id, account_id, application_id),
            )

    def authorize_bridge(self, account_id, bridge_id):
        self.account(account_id)
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM identity_bridge_binding WHERE bridge_id=?", (bridge_id,)
            ).fetchone()
        if row:
            self.client(row["application_id"])
            if row["account_id"] != account_id or not self.has_grant(
                account_id, row["application_id"]
            ):
                raise CoreError("应用授权已撤销", "forbidden")

    def bound_bridges(self, account_id, application_id):
        with self.connection() as db:
            return [
                row["bridge_id"]
                for row in db.execute(
                    "SELECT bridge_id FROM identity_bridge_binding WHERE account_id=? AND application_id=?",
                    (account_id, application_id),
                )
            ]
