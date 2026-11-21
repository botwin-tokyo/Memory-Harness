from __future__ import annotations

import json
import sqlite3
from typing import Any

from memory_harness.domain.scope import effective_scopes
from memory_harness.models.api import ChangeItem, ChangesOut, Tombstone
from memory_harness.models.domain import Principal

_TARGET_TABLES = {
    "fact": ("facts", "fact_id"),
    "preference": ("preferences", "preference_id"),
    "episode": ("episodes", "episode_id"),
    "note": ("notes", "note_id"),
    "credential": ("notes", "note_id"),
    "procedure": ("procedures", "procedure_id"),
    "core": ("core_memory", "slot_id"),
    "link": ("memory_links", "link_id"),
}


def _is_tombstone(event_type: str) -> bool:
    return (
        event_type.endswith("_superseded")
        or event_type.endswith("_retracted")
        or event_type == "forget_executed"
    )


def _resolve_record(
    con: sqlite3.Connection, target_type: str | None, target_id: str | None
) -> dict[str, Any] | None:
    'The current state of the mutated record (None if unknown/gone).'
    if target_type is None or target_id is None:
        return None
    tbl = _TARGET_TABLES.get(target_type)
    if tbl is None:
        return None
    table, pk = tbl
    row = con.execute(f"SELECT * FROM {table} WHERE {pk}=?", (target_id,)).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _item(con: sqlite3.Connection, r: sqlite3.Row) -> ChangeItem:
    return ChangeItem(
        seq=int(r["seq"]),
        event_id=str(r["event_id"]),
        event_type=str(r["event_type"]),
        recorded_at=int(r["recorded_at"]),
        scope=str(r["scope"]),
        source_trust=str(r["source_trust"]),
        created_by_agent=str(r["created_by_agent"]),
        created_on_node=str(r["created_on_node"]),
        learning_eligible=bool(r["learning_eligible"]),
        redacted=bool(r["redacted"]),
        emotion_id=int(r["emotion_id"]),
        content=r["content"] if r["content"] is None else str(r["content"]),
        summary=r["summary"] if r["summary"] is None else str(r["summary"]),
        about=[str(x) for x in json.loads(str(r["about"]))],
        tags=[str(x) for x in json.loads(str(r["tags"]))],
        target_type=None if r["target_type"] is None else str(r["target_type"]),
        target_id=None if r["target_id"] is None else str(r["target_id"]),
        record=_resolve_record(
            con,
            None if r["target_type"] is None else str(r["target_type"]),
            None if r["target_id"] is None else str(r["target_id"]),
        ),
    )


def changes(
    con: sqlite3.Connection,
    p: Principal,
    *,
    since: int,
    scope: str | None = None,
    types: list[str] | None = None,
    learning_eligible: bool | None = None,
    limit: int = 500,
) -> ChangesOut:
    scopes = effective_scopes(p, [scope] if scope else None)
    if not scopes:
        return ChangesOut(items=[], tombstones=[], next_cursor=since)
    scope_ph = ",".join("?" for _ in scopes)
    sql = f"SELECT * FROM events WHERE seq>? AND scope IN ({scope_ph})"
    params: list[object] = [since, *sorted(scopes)]
    if learning_eligible is not None:
        sql += " AND learning_eligible=?"
        params.append(1 if learning_eligible else 0)
    if types:
        type_ph = ",".join("?" for _ in types)
        sql += f" AND event_type IN ({type_ph})"
        params.extend(types)
    sql += " ORDER BY seq LIMIT ?"
    params.append(limit)

    rows = con.execute(sql, params).fetchall()
    items: list[ChangeItem] = []
    tombstones: list[Tombstone] = []
    for r in rows:
        if _is_tombstone(str(r["event_type"])):
            tombstones.append(
                Tombstone(
                    target_type=None if r["target_type"] is None else str(r["target_type"]),
                    target_id=None if r["target_id"] is None else str(r["target_id"]),
                )
            )
        else:
            items.append(_item(con, r))
    return ChangesOut(
        items=items,
        tombstones=tombstones,
        next_cursor=int(rows[-1]["seq"]) if rows else since,
    )
