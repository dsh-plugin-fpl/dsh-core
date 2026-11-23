"""Owned account import: explicit primary mapping, no imported browser sessions."""

from uuid import UUID, uuid5


def import_accounts(accounts, rows, primary_source, source_id):
    mapping = {primary_source: accounts.initial["id"]}
    profiles = {row["id"]: row for row in accounts.profiles()}
    with accounts.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in rows:
            identity = str(UUID(row["id"]))
            if identity == primary_source:
                continue
            matched = db.execute(
                "SELECT username,password FROM account WHERE id=?", (identity,)
            ).fetchone()
            if identity in profiles and not (
                matched and matched[0] == row["username"] and matched[1] == row["password_hash"]
            ):
                identity = str(uuid5(UUID(source_id), "account:" + identity))
            mapping[row["id"]] = identity
            username = row["username"][:64]
            conflict = db.execute(
                "SELECT id FROM account WHERE username=? COLLATE NOCASE", (username,)
            ).fetchone()
            if conflict and conflict[0] != identity:
                username = username[:47] + "-legacy-" + identity[:8]
            encoded = row["password_hash"]
            if not isinstance(encoded, str) or not encoded.startswith("$argon2id$"):
                raise ValueError("旧账号密码摘要格式不受支持")
            previous = db.execute("SELECT username FROM account WHERE id=?", (identity,)).fetchone()
            if previous and previous[0] != username:
                raise ValueError("旧账号与已导入账号冲突")
            db.execute(
                "INSERT INTO account(id,username,display_name,password,role,active,revision,legacy) VALUES(?,?,?,?,?,?,?,0) ON CONFLICT(id) DO NOTHING",
                (
                    identity,
                    username,
                    row["display_name"],
                    encoded,
                    "admin" if row["platform_role"] == "admin" else "user",
                    int(row["status"] == "active"),
                    row["revision"],
                ),
            )
    return mapping
