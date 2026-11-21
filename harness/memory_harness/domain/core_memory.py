from __future__ import annotations

import sqlite3

from memory_harness import ids
from memory_harness.db.writer import WriteQueue
from memory_harness.domain.events import emit_mutation_event
from memory_harness.domain.scope import Forbidden
from memory_harness.domain.supersession import write_memory
from memory_harness.domain.trust import tier_ge
from memory_harness.models.api import CoreIn, CoreOut, CoreSlotOut
from memory_harness.models.domain import MemoryWrite, Principal
from memory_harness.time import now_ms


class CoreNotFound(Exception):
    'No slot with that (owner_scope, slot_key). Maps to HTTP 404.'


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text.split()) * 1.3))


def _fit(slots: list[sqlite3.Row], cap: int) -> list[sqlite3.Row]:
    out: list[sqlite3.Row] = []
    total = 0
    for s in slots:
        if s["pinned"]:
            out.append(s)
            total += int(s["token_estimate"])
        elif total + int(s["token_estimate"]) > cap:
            continue
        else:
            out.append(s)
            total += int(s["token_estimate"])
    return out


def compose_core(
    con: sqlite3.Connection,
    p: Principal,
    budget: int,
    *,
    shared_budget: int,
    agent_budget: int,
) -> list[sqlite3.Row]:
    anchors = con.execute(
        "SELECT * FROM core_memory WHERE owner_scope='shared' AND pinned=1"
        " AND subject_class='self' ORDER BY priority DESC"
    ).fetchall()
    companion_anchors = con.execute(
        "SELECT * FROM core_memory WHERE owner_scope='shared' AND pinned=1"
        " AND subject_class='companion' ORDER BY priority DESC"
    ).fetchall()
    rest = con.execute(
        "SELECT * FROM core_memory WHERE owner_scope='shared'"
        " AND NOT (pinned=1 AND subject_class IN ('self','companion'))"
        " ORDER BY pinned DESC, priority DESC"
    ).fetchall()
    mine = con.execute(
        "SELECT * FROM core_memory WHERE owner_scope=?"
        " ORDER BY pinned DESC, priority DESC",
        (f"private:{p.principal_id}",),
    ).fetchall()

    shared_part = _fit(anchors + companion_anchors, shared_budget)
    used = sum(int(s["token_estimate"]) for s in shared_part)
    shared_part += _fit(rest, max(0, shared_budget - used))
    return shared_part + _fit(mine, min(agent_budget, budget))


def _slot_out(row: sqlite3.Row) -> CoreSlotOut:
    return CoreSlotOut(
        slot_id=str(row["slot_id"]),
        slot_key=str(row["slot_key"]),
        owner_scope=str(row["owner_scope"]),
        value_text=str(row["value_text"]),
        priority=int(row["priority"]),
        pinned=bool(row["pinned"]),
        token_estimate=int(row["token_estimate"]),
    )


async def edit_core(
    w: WriteQueue,
    p: Principal,
    c: CoreIn,
    *,
    shared_budget: int,
    agent_budget: int,
) -> CoreOut:
    'One core edit + overflow demotion, atomically. Emits `core_edited`.'

    def txn(cur: sqlite3.Cursor) -> CoreOut:
        owner = c.owner_scope or f"private:{p.principal_id}"
        if owner == "shared":
            if not tier_ge(p.trust_ceiling, "trusted_tool"):
                raise Forbidden("shared core requires user-tier or grant")
        elif owner != f"private:{p.principal_id}":
            raise Forbidden("cannot edit another agent's core")

        slot: CoreSlotOut | None = None
        if c.op == "append":
            slot_id = ids.uuid7()
            cur.execute(
                "INSERT INTO core_memory(slot_id, slot_key, owner_scope, value_text,"
                " priority, token_estimate, created_by_agent, about, tags,"
                " updated_at, pinned, subject_class) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    slot_id,
                    c.slot_key,
                    owner,
                    c.value,
                    c.priority,
                    estimate_tokens(c.value or ""),
                    p.principal_id,
                    _json(c.about),
                    _json(c.tags),
                    now_ms(),
                    1 if c.pinned else 0,
                    c.subject_class or "world",
                ),
            )
            slot = _slot_out(
                cur.execute("SELECT * FROM core_memory WHERE slot_id=?", (slot_id,)).fetchone()
            )
            target_id = slot_id
        elif c.op == "replace":
            row = cur.execute(
                "SELECT * FROM core_memory WHERE owner_scope=? AND slot_key=?"
                " ORDER BY priority DESC LIMIT 1",
                (owner, c.slot_key),
            ).fetchone()
            if row is None:
                raise CoreNotFound(f"{owner}/{c.slot_key}")
            cur.execute(
                "UPDATE core_memory SET value_text=?, priority=?, pinned=?,"
                " token_estimate=?, updated_at=? WHERE slot_id=?",
                (
                    c.value,
                    c.priority,
                    1 if c.pinned else 0,
                    estimate_tokens(c.value or ""),
                    now_ms(),
                    row["slot_id"],
                ),
            )
            slot = _slot_out(
                cur.execute(
                    "SELECT * FROM core_memory WHERE slot_id=?", (row["slot_id"],)
                ).fetchone()
            )
            target_id = str(row["slot_id"])
        else:
            row = cur.execute(
                "SELECT slot_id FROM core_memory WHERE owner_scope=? AND slot_key=?"
                " ORDER BY priority DESC LIMIT 1",
                (owner, c.slot_key),
            ).fetchone()
            if row is None:
                raise CoreNotFound(f"{owner}/{c.slot_key}")
            cur.execute("DELETE FROM core_memory WHERE slot_id=?", (row["slot_id"],))
            target_id = str(row["slot_id"])

        budget = shared_budget if owner == "shared" else agent_budget
        demoted: list[str] = []
        while True:
            total = int(
                cur.execute(
                    "SELECT COALESCE(SUM(token_estimate),0) FROM core_memory"
                    " WHERE owner_scope=?",
                    (owner,),
                ).fetchone()[0]
            )
            if total <= budget:
                break
            victim = cur.execute(
                "SELECT * FROM core_memory WHERE owner_scope=? AND pinned=0"
                " ORDER BY priority ASC, updated_at ASC LIMIT 1",
                (owner,),
            ).fetchone()
            if victim is None:
                break
            cur.execute("DELETE FROM core_memory WHERE slot_id=?", (victim["slot_id"],))
            write_memory(
                cur,
                MemoryWrite(
                    type="note",
                    body=str(victim["value_text"]),
                    scope=owner,
                    emotion_id=127,
                    source_trust=p.trust_ceiling,
                    created_by_agent=p.principal_id,
                    created_on_node=p.node,
                    learning_eligible=p.learning_eligible_default,
                    about=c.about,
                    tags=c.tags,
                ),
            )
            demoted.append(str(victim["slot_id"]))

        emit_mutation_event(
            cur,
            event_type="core_edited",
            created_by_agent=p.principal_id,
            created_on_node=p.node,
            source_trust=p.trust_ceiling,
            scope=owner,
            about=c.about,
            tags=c.tags,
            learning_eligible=p.learning_eligible_default,
            target_type="core",
            target_id=target_id,
            emotion_id=127,
            summary=f"{c.op} {c.slot_key}",
            content=_json(
                {
                    "op": c.op,
                    "slot_key": c.slot_key,
                    "slot": _slot_snapshot(cur, target_id),
                    "demoted_slot_ids": demoted,
                }
            ),
        )
        return CoreOut(slot=slot, demoted=demoted)

    return await w.tx(txn)


def _slot_snapshot(cur: sqlite3.Cursor, slot_id: str) -> dict[str, object] | None:
    row = cur.execute(
        "SELECT * FROM core_memory WHERE slot_id=?", (slot_id,)
    ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _json(obj: object) -> str:
    import json

    return json.dumps(obj)
