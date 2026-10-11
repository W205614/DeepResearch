"""Assemble the same public run fields for both database adapters."""
import json

from .reliability import enrich_run


async def read_owned_run(db, run_id: str, user: str) -> dict | None:
    row = await db.one("SELECT * FROM runs WHERE id=? AND user_id=?", (run_id, user))
    if row is None:
        return None
    row["attachments"] = await db.rows(
        "SELECT id,name,media_type,size,position FROM attachments "
        "WHERE run_id=? AND status='ready' ORDER BY position", (run_id,))
    row["sources"] = json.loads(row["sources"])
    row["validation"] = json.loads(row["validation"])
    row["usage"] = await db.one("SELECT * FROM counters WHERE run_id=?", (run_id,)) or {}
    enrich_run(row, getattr(db, "reliability_settings", None))
    return row
