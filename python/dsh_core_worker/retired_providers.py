"""One-way retirement of obsolete provider configuration, preserving owned history."""

import json

from .store import TERMINAL, encode

RETIRED_PROVIDER_KINDS = frozenset(
    {
        "senseaudio_image",
        "senseaudio_video",
        "senseaudio_asr",
        "senseaudio_asr_pro",
        "senseaudio_realtime",
        "senseaudio_music",
        "senseaudio_sfx",
    }
)
REMOVED_ERROR = {"code": "provider_removed", "message": "此供应商适配器已移除，任务不能继续执行"}


def retired_task(task):
    return task.get("connection", {}).get("kind") in RETIRED_PROVIDER_KINDS


def retire_providers(store):
    """Run under the profile lock before any task recovery; rollback on all failures.

    Inspect actual records on every startup so imported old profiles are covered too.
    No-op runs leave revisions unchanged. Credentials shared by a retained connection
    are kept; only the retired providers' routing and exclusive secrets are removed.
    """
    store.db.execute("BEGIN IMMEDIATE")
    try:
        catalog_state = store.config("catalog", {})
        catalog = catalog_state["value"]
        connection_state = store.config("connections", [])
        connections = connection_state["value"]
        removed = [p for p in catalog.get("providers", []) if p["kind"] in RETIRED_PROVIDER_KINDS]
        old_connections = [p for p in connections if p["kind"] in RETIRED_PROVIDER_KINDS]
        removed_ids = {p["id"] for p in [*removed, *old_connections]}
        model_ids = {m["id"] for p in removed for m in p.get("models", [])}
        kept = [p for p in connections if p["id"] not in removed_ids]
        refs = {"PROVIDER_" + identity.replace("-", "_") for identity in removed_ids}
        refs.update(p["credential_ref"] for p in old_connections if p.get("credential_ref"))
        retained_refs = {
            "PROVIDER_" + p["id"].replace("-", "_")
            for p in catalog.get("providers", [])
            if p["id"] not in removed_ids
        }
        retained_refs.update(p["credential_ref"] for p in kept if p.get("credential_ref"))
        for row in store.db.execute("SELECT value FROM tasks").fetchall():
            task = json.loads(row[0])
            if not (
                retired_task(task) or task.get("request", {}).get("connection_id") in removed_ids
            ):
                if task.get("connection", {}).get("credential_ref"):
                    retained_refs.add(task["connection"]["credential_ref"])
                continue
            if task.get("connection", {}).get("credential_ref"):
                refs.add(task["connection"]["credential_ref"])
            # Keep the original snapshot even if routing records were already removed.
            if task["status"] not in TERMINAL:
                store.update(task["id"], status="interrupted", error=REMOVED_ERROR)
        refs.difference_update(retained_refs)
        updates = {}
        if removed:
            catalog["providers"] = [p for p in catalog["providers"] if p["id"] not in removed_ids]
        defaults = catalog.get("default_model_ids", {})
        filtered_defaults = {k: v for k, v in defaults.items() if v not in model_ids}
        if removed or filtered_defaults != defaults:
            catalog["default_model_ids"] = filtered_defaults
            updates["catalog"] = (catalog_state, catalog)
        if kept != connections:
            updates["connections"] = (connection_state, kept)
        state = store.config("defaults", {})
        defaults = state["value"]
        filtered = {k: v for k, v in defaults.items() if v.get("connection_id") not in removed_ids}
        if filtered != defaults:
            updates["defaults"] = (state, filtered)
        for key, (state, value) in updates.items():
            store.db.execute(
                "INSERT INTO configs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,value=excluded.value",
                (key, state["revision"] + 1, encode(value)),
            )
        for ref in refs:
            store.db.execute("DELETE FROM configs WHERE id=?", ("credential:" + ref,))
        store.db.execute("COMMIT")
        return len(removed_ids)
    except BaseException:
        store.db.execute("ROLLBACK")
        raise
