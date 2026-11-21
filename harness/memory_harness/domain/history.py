from __future__ import annotations

import sqlite3

from memory_harness.domain.scope import visible_scopes
from memory_harness.models.api import RevisionOut
from memory_harness.models.domain import Principal

_TABLES = {
    "fact": ("facts", "fact_id", "gloss", "recorded_at"),
    "preference": ("preferences", "preference_id", "statement", "last_confirmed_at"),
    "episode": ("episodes", "episode_id", "summary", None),
    "note": ("notes", "note_id", "body", "recorded_at"),
    "procedure": ("procedures", "procedure_id", "steps_md", None),
}


class LineageNotFound(Exception):
    'No revisions of this lineage visible to the caller. Maps to HTTP 404.'


def history(
    con: sqlite3.Connection, p: Principal, lineage_id: str
) -> tuple[str, list[RevisionOut]]:
    '(memory_type, revisions oldest→newest). Raises LineageNotFound.'
    visible = visible_scopes(p)
    for mtype, (table, pk, text_col, ts_col) in _TABLES.items():
        rows = con.execute(
            f"SELECT * FROM {table} WHERE lineage_id=? ORDER BY revision ASC, rowid ASC",
            (lineage_id,),
        ).fetchall()
        if not rows:
            continue
        out: list[RevisionOut] = []
        for r in rows:
            if str(r["scope"]) not in visible:
                continue
            status = (
                str(r["status"])
                if "status" in r.keys() and r["status"] is not None
                else ("active" if r["active"] else "superseded")
            )
            out.append(
                RevisionOut(
                    id=str(r[pk]),
                    revision=int(r["revision"]),
                    status=status,
                    is_head=bool(r["is_head"]),
                    scope=str(r["scope"]),
                    source_trust=str(r["source_trust"]),
                    confidence=float(r["confidence"]),
                    emotion_id=int(r["emotion_id"]),
                    text=str(r[text_col]),
                    valid_from=(
                        int(r["valid_from"]) if table == "facts" else None
                    ),
                    valid_to=(
                        None if table != "facts" or r["valid_to"] is None
                        else int(r["valid_to"])
                    ),
                    recorded_at=(
                        None if ts_col is None or r[ts_col] is None else int(r[ts_col])
                    ),
                    created_by_agent=str(r["created_by_agent"]),
                )
            )
        if out:
            return mtype, out
    raise LineageNotFound(lineage_id)
