from __future__ import annotations

import sqlite3

from memory_harness.domain.events import emit_mutation_event
from memory_harness.domain.scope import Forbidden
from memory_harness.models.api import ConflictFactOut, ConflictGroupOut
from memory_harness.models.domain import Principal
from memory_harness.time import now_ms


def _require_owner(p: Principal) -> None:
    if p.trust_ceiling != "user":
        raise Forbidden("adjudication is owner-only")


def _windows_overlap(a: sqlite3.Row, b: sqlite3.Row) -> bool:
    now = now_ms()
    a_to = int(a["valid_to"]) if a["valid_to"] is not None else now
    b_to = int(b["valid_to"]) if b["valid_to"] is not None else now
    return int(a["valid_from"]) <= b_to and int(b["valid_from"]) <= a_to


def _group_out(members: list[sqlite3.Row]) -> ConflictGroupOut:
    first = members[0]
    return ConflictGroupOut(
        subject_ref=str(first["subject_ref"]),
        predicate=str(first["predicate"]),
        scope=str(first["scope"]),
        source_trust=str(first["source_trust"]),
        facts=[
            ConflictFactOut(
                fact_id=str(m["fact_id"]),
                lineage_id=str(m["lineage_id"]),
                gloss=str(m["gloss"]),
                object_json=str(m["object_json"]),
                confidence=float(m["confidence"]),
                valid_from=int(m["valid_from"]),
                valid_to=None if m["valid_to"] is None else int(m["valid_to"]),
                created_by_agent=str(m["created_by_agent"]),
            )
            for m in members
        ],
    )


def _snapshot(cur: sqlite3.Cursor, fact_id: str) -> str:
    import json

    row = cur.execute("SELECT * FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
    return json.dumps({k: row[k] for k in row.keys()})


def detect_and_mark(cur: sqlite3.Cursor, p: Principal) -> list[ConflictGroupOut]:
    _require_owner(p)
    groups: list[ConflictGroupOut] = []
    candidates = cur.execute(
        "SELECT subject_ref, predicate, scope, source_trust, COUNT(*) c"
        " FROM facts WHERE status='active' AND is_head=1"
        " GROUP BY subject_ref, predicate, scope, source_trust HAVING c > 1"
    ).fetchall()
    for g in candidates:
        if str(g["predicate"]) == "states" and str(g["subject_ref"]) in ("self", "world"):
            continue
        members = cur.execute(
            "SELECT * FROM facts WHERE subject_ref=? AND predicate=? AND scope=?"
            " AND source_trust=? AND status='active' AND is_head=1",
            (g["subject_ref"], g["predicate"], g["scope"], g["source_trust"]),
        ).fetchall()
        conflict = [
            m for m in members
            if any(_windows_overlap(m, o) and m["object_json"] != o["object_json"]
                   for o in members if o["fact_id"] != m["fact_id"])
        ]
        if len(conflict) < 2:
            continue
        for m in conflict:
            cur.execute(
                "UPDATE facts SET status='disputed' WHERE fact_id=?", (m["fact_id"],)
            )
            emit_mutation_event(
                cur, event_type="fact_disputed",
                created_by_agent="adjudication", created_on_node="memory-node",
                source_trust=str(m["source_trust"]), scope=str(m["scope"]),
                about=[], tags=[], learning_eligible=False,
                target_type="fact", target_id=str(m["fact_id"]),
                emotion_id=int(m["emotion_id"]), content=_snapshot(cur, str(m["fact_id"])),
            )
        groups.append(_group_out(conflict))
    return groups


def resolve(
    cur: sqlite3.Cursor, p: Principal, winner_id: str, loser_id: str
) -> tuple[sqlite3.Row, sqlite3.Row]:
    _require_owner(p)
    winner = cur.execute("SELECT * FROM facts WHERE fact_id=?", (winner_id,)).fetchone()
    loser = cur.execute("SELECT * FROM facts WHERE fact_id=?", (loser_id,)).fetchone()
    if winner is None or loser is None:
        raise ValueError("unknown fact id")
    if str(winner["status"]) != "disputed" or str(loser["status"]) != "disputed":
        raise ValueError("both facts must be disputed (in the adjudication queue)")
    same_group = (
        winner["subject_ref"] == loser["subject_ref"]
        and winner["predicate"] == loser["predicate"]
        and winner["scope"] == loser["scope"]
        and winner["source_trust"] == loser["source_trust"]
    )
    if not same_group:
        raise ValueError("winner and loser are not in the same conflict group")

    now = now_ms()
    cur.execute("UPDATE facts SET status='active' WHERE fact_id=?", (winner_id,))
    emit_mutation_event(
        cur, event_type="fact_revised", created_by_agent=p.principal_id,
        created_on_node=p.node, source_trust=p.trust_ceiling,
        scope=str(winner["scope"]), about=[], tags=[], learning_eligible=True,
        target_type="fact", target_id=winner_id, emotion_id=int(winner["emotion_id"]),
        content=_snapshot(cur, winner_id),
    )
    import json as _json

    cur.execute("DELETE FROM fts_memories WHERE ref_id=?", (winner_id,))
    cur.execute(
        "INSERT INTO fts_memories(body, about, tags, kind, ref_id) VALUES (?,?,?,?,?)",
        (winner["gloss"], winner["about"], winner["tags"], "fact", winner_id),
    )
    from memory_harness.workers import queue

    queue.enqueue(cur, "embed", {"ref_type": "fact", "ref_id": winner_id})

    cur.execute(
        "UPDATE facts SET status='superseded', is_head=0, valid_to=? WHERE fact_id=?",
        (now, loser_id),
    )
    cur.execute("DELETE FROM fts_memories WHERE ref_id=?", (loser_id,))
    emit_mutation_event(
        cur, event_type="fact_superseded", created_by_agent=p.principal_id,
        created_on_node=p.node, source_trust=p.trust_ceiling,
        scope=str(loser["scope"]), about=[], tags=[], learning_eligible=True,
        target_type="fact", target_id=loser_id, emotion_id=int(loser["emotion_id"]),
        content=_snapshot(cur, loser_id),
    )
    return winner, loser
