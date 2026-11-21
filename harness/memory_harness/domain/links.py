from __future__ import annotations

import json
import sqlite3

from memory_harness import ids
from memory_harness.domain.events import emit_mutation_event
from memory_harness.domain.scope import Forbidden, visible_scopes
from memory_harness.models.domain import Principal
from memory_harness.time import now_ms

CONTROLLED_LINK_TYPES = {
    "elaborates",
    "relates_to",
    "example_of",
    "part_of",
    "caused_by",
    "detailed_by",
    "contradicts",
}
MAX_FREE_LINK_TYPE_LEN = 64

_LINEAGE_TABLES = ("facts", "preferences", "episodes", "notes", "procedures")


class LineageNotFound(Exception):
    'No HEAD row for this lineage in any memory table. Maps to HTTP 404.'


def find_lineage_head(cur: sqlite3.Cursor | sqlite3.Connection, lineage_id: str) -> tuple[str, sqlite3.Row] | None:
    '(table, HEAD row) for a lineage in any memory table, or None.'
    for table in _LINEAGE_TABLES:
        row = cur.execute(
            f"SELECT * FROM {table} WHERE lineage_id=? AND is_head=1 LIMIT 1",
            (lineage_id,),
        ).fetchone()
        if row is not None:
            return table, row
    return None


def require_visible(
    p: Principal, cur: sqlite3.Cursor | sqlite3.Connection, lineage_id: str
) -> tuple[str, sqlite3.Row]:
    found = find_lineage_head(cur, lineage_id)
    if found is None:
        raise LineageNotFound(lineage_id)
    table, row = found
    if str(row["scope"]) not in visible_scopes(p):
        raise Forbidden(f"lineage not visible: {lineage_id}")
    return table, row


def validate_link_type(link_type: str) -> str:
    lt = link_type.strip()
    if not lt or len(lt) > MAX_FREE_LINK_TYPE_LEN:
        raise ValueError(f"invalid link_type: {link_type!r}")
    return lt


def create_link(
    cur: sqlite3.Cursor,
    src: str,
    dst: str,
    link_type: str,
    status: str,
    *,
    proposed_by: str,
    curated_by: str | None,
    scope: str,
    weight: float = 1.0,
    source_trust: str = "agent",
    created_on_node: str = "unknown",
    learning_eligible: bool = True,
) -> dict[str, str]:
    link_id = ids.uuid7()
    now = now_ms()
    cur.execute(
        "INSERT INTO memory_links(link_id, src_lineage_id, dst_lineage_id, link_type,"
        " weight, status, proposed_by, curated_by, scope, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (link_id, src, dst, link_type, weight, status, proposed_by, curated_by,
         scope, now, now),
    )
    row = cur.execute(
        "SELECT * FROM memory_links WHERE link_id=?", (link_id,)
    ).fetchone()
    snapshot = json.dumps({k: row[k] for k in row.keys()})
    emit_mutation_event(
        cur,
        event_type="link_activated" if status == "active" else "link_proposed",
        created_by_agent=proposed_by,
        created_on_node=created_on_node,
        source_trust=source_trust,
        scope=scope,
        about=[],
        tags=[],
        learning_eligible=learning_eligible,
        target_type="link",
        target_id=link_id,
        emotion_id=127,
        content=snapshot,
    )
    return {"link_id": link_id, "link_type": link_type, "target": dst, "status": status}


def transition_link(
    cur: sqlite3.Cursor,
    link_id: str,
    action: str,
    *,
    curated_by: str,
    source_trust: str,
    created_on_node: str,
    learning_eligible: bool = True,
) -> dict[str, str]:
    row = cur.execute(
        "SELECT * FROM memory_links WHERE link_id=?", (link_id,)
    ).fetchone()
    if row is None:
        raise LineageNotFound(f"link: {link_id}")
    status = str(row["status"])
    if action == "retract":
        if status != "active":
            raise ValueError(f"link {link_id} is {status}, not active")
    elif status != "proposed":
        raise ValueError(f"link {link_id} is {status}, not proposed")
    new_status = "active" if action == "promote" else "rejected"
    cur.execute(
        "UPDATE memory_links SET status=?, curated_by=?, updated_at=? WHERE link_id=?",
        (new_status, curated_by, now_ms(), link_id),
    )
    updated = cur.execute(
        "SELECT * FROM memory_links WHERE link_id=?", (link_id,)
    ).fetchone()
    emit_mutation_event(
        cur,
        event_type="link_activated" if new_status == "active" else "link_rejected",
        created_by_agent=curated_by,
        created_on_node=created_on_node,
        source_trust=source_trust,
        scope=str(row["scope"]),
        about=[],
        tags=[],
        learning_eligible=learning_eligible,
        target_type="link",
        target_id=link_id,
        emotion_id=127,
        content=json.dumps({k: updated[k] for k in updated.keys()}),
    )
    return {
        "link_id": link_id,
        "link_type": str(row["link_type"]),
        "target": str(row["dst_lineage_id"]),
        "status": new_status,
    }
