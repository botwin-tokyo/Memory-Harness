from __future__ import annotations

import sqlite3

from memory_harness.db.writer import WriteQueue
from memory_harness.domain.events import emit_mutation_event
from memory_harness.domain.scope import Forbidden, visible_scopes
from memory_harness.models.api import ForgetIn
from memory_harness.models.domain import Principal

_TABLES = {
    "fact": ("facts", "fact_id"),
    "preference": ("preferences", "preference_id"),
    "episode": ("episodes", "episode_id"),
    "note": ("notes", "note_id"),
    "credential": ("notes", "note_id"),
    "contact": ("notes", "note_id"),
    "procedure": ("procedures", "procedure_id"),
    "core": ("core_memory", "slot_id"),
}


def _resolve_targets(
    cur: sqlite3.Cursor, f: ForgetIn
) -> list[tuple[str, str, str, sqlite3.Row]]:
    '(table, pk, target_type, row) for the id/selector given.'
    out: list[tuple[str, str, str, sqlite3.Row]] = []
    sel = f.selector or {}
    sel_type = sel.get("type")
    sel_lineage = sel.get("lineage_id")
    sel_id = sel.get("id") or f.id

    if sel_type and sel_lineage and sel_type in _TABLES:
        table, pk = _TABLES[sel_type]
        rows = cur.execute(
            f"SELECT * FROM {table} WHERE lineage_id=?", (sel_lineage,)
        ).fetchall()
        out.extend((table, pk, sel_type, r) for r in rows)
        return out

    if sel_id:
        if sel_type and sel_type in _TABLES:
            table, pk = _TABLES[sel_type]
            row = cur.execute(f"SELECT * FROM {table} WHERE {pk}=?", (sel_id,)).fetchone()
            if row is not None:
                out.append((table, pk, sel_type, row))
            return out
        for ttype, (table, pk) in _TABLES.items():
            row = cur.execute(f"SELECT * FROM {table} WHERE {pk}=?", (sel_id,)).fetchone()
            if row is not None:
                out.append((table, pk, ttype, row))
                return out
        for ttype, (table, pk) in _TABLES.items():
            if table == "core_memory":
                continue
            rows = cur.execute(
                f"SELECT * FROM {table} WHERE lineage_id=?", (sel_id,)
            ).fetchall()
            out.extend((table, pk, ttype, r) for r in rows)
        return out
    return out


def _retract(cur: sqlite3.Cursor, table: str, pk: str, row_id: str) -> None:
    'Retract the canonical row + purge it from every index.'
    if table in ("facts", "episodes", "notes"):
        cur.execute(
            f"UPDATE {table} SET status='retracted', is_head=0 WHERE {pk}=?", (row_id,)
        )
    elif table == "preferences":
        cur.execute(
            "UPDATE preferences SET active=0, is_head=0 WHERE preference_id=?", (row_id,)
        )
    elif table == "procedures":
        cur.execute(
            "UPDATE procedures SET is_head=0, validation_status='retracted'"
            " WHERE procedure_id=?",
            (row_id,),
        )
    else:
        cur.execute("DELETE FROM core_memory WHERE slot_id=?", (row_id,))
    cur.execute("DELETE FROM fts_memories WHERE ref_id=?", (row_id,))
    for vec_table in ("vec", "vec_library"):
        if cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (vec_table,),
        ).fetchone():
            cur.execute(f"DELETE FROM {vec_table} WHERE ref_id=?", (row_id,))


def _already_retracted(table: str, row: sqlite3.Row) -> bool:
    'Has this row already been forgotten?'
    if table in ("facts", "episodes", "notes"):
        return str(row["status"]) == "retracted"
    if table == "preferences":
        return not bool(row["active"])
    if table == "procedures":
        return str(row["validation_status"]) == "retracted"
    return False


async def forget(w: WriteQueue, p: Principal, f: ForgetIn) -> int:
    'Retract by id/selector, cascade to indexes/core, emit tombstones.'

    def txn(cur: sqlite3.Cursor) -> int:
        visible = visible_scopes(p)
        n = 0
        for table, pk, ttype, row in _resolve_targets(cur, f):
            scope = str(row["scope"]) if table != "core_memory" else str(row["owner_scope"])
            if scope not in visible:
                continue
            if table == "notes" and row["note_type"] == "contact":
                raise Forbidden("contact records and portraits are permanently retained")
            if _already_retracted(table, row):
                continue
            row_id = str(row[pk])
            _retract(cur, table, pk, row_id)
            promoted = [
                str(r["slot_id"])
                for r in cur.execute(
                    "SELECT slot_id FROM core_memory WHERE promoted_from=?", (row_id,)
                ).fetchall()
            ]
            cur.execute("DELETE FROM core_memory WHERE promoted_from=?", (row_id,))
            emit_mutation_event(
                cur,
                event_type="forget_executed",
                created_by_agent=p.principal_id,
                created_on_node=p.node,
                source_trust=p.trust_ceiling,
                scope=scope,
                about=[],
                tags=[],
                learning_eligible=False,
                target_type=ttype,
                target_id=row_id,
                emotion_id=127,
                summary=f.reason,
                content=_json(
                    {
                        "retracted": [{"table": table, "pk": pk, "row_id": row_id}],
                        "core_slots_deleted": promoted,
                    }
                ),
            )
            n += 1
        return n

    return await w.tx(txn)


def _json(obj: object) -> str:
    import json

    return json.dumps(obj)
