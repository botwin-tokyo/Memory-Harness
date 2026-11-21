from __future__ import annotations

import json
import sqlite3

from memory_harness.domain.events import emit_mutation_event
from memory_harness.domain.scope import Forbidden
from memory_harness.models.domain import Principal
from memory_harness.time import now_ms


class ProcedureNotFound(Exception):
    'Maps to HTTP 404.'


def search(
    con: sqlite3.Connection, query: str, scopes: set[str], max_n: int
) -> list[tuple[sqlite3.Row, float]]:
    if not scopes:
        return []
    terms = {t.lower() for t in query.split() if len(t) > 2}
    if not terms:
        return []
    ph = ",".join("?" for _ in scopes)
    rows = con.execute(
        f"SELECT * FROM procedures WHERE is_head=1"
        f" AND validation_status IN ('candidate','validated') AND scope IN ({ph})",
        tuple(sorted(scopes)),
    ).fetchall()
    out: list[tuple[sqlite3.Row, float]] = []
    for r in rows:
        haystack = set(str(r["name"]).lower().split()) | {
            str(t).lower() for t in json.loads(str(r["intent_tags"]))
        }
        hits = len(terms & haystack)
        if hits:
            out.append((r, hits / len(terms)))
    out.sort(key=lambda t: t[1], reverse=True)
    return out[:max_n]


def validate(cur: sqlite3.Cursor, p: Principal, procedure_id: str) -> sqlite3.Row:
    'Owner-only validation gate: candidate → validated. Idempotent.'
    if p.trust_ceiling != "user":
        raise Forbidden("procedure validation is owner-only")
    row = cur.execute(
        "SELECT * FROM procedures WHERE procedure_id=?", (procedure_id,)
    ).fetchone()
    if row is None:
        raise ProcedureNotFound(procedure_id)
    if str(row["validation_status"]) != "validated":
        cur.execute(
            "UPDATE procedures SET validation_status='validated', last_validated_at=?"
            " WHERE procedure_id=?",
            (now_ms(), procedure_id),
        )
        updated = cur.execute(
            "SELECT * FROM procedures WHERE procedure_id=?", (procedure_id,)
        ).fetchone()
        emit_mutation_event(
            cur, event_type="procedure_validated",
            created_by_agent=p.principal_id, created_on_node=p.node,
            source_trust=p.trust_ceiling, scope=str(row["scope"]), about=[], tags=[],
            learning_eligible=True, target_type="procedure", target_id=procedure_id,
            emotion_id=int(row["emotion_id"]),
            content=json.dumps({k: updated[k] for k in updated.keys()}),
        )
    return cur.execute(
        "SELECT * FROM procedures WHERE procedure_id=?", (procedure_id,)
    ).fetchone()
