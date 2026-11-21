from __future__ import annotations

import asyncio
import sqlite3

from memory_harness.db import meta
from memory_harness.db.writer import WriteQueue
from memory_harness.domain.events import emit_mutation_event
from memory_harness.domain.supersession import write_memory
from memory_harness.models.domain import MemoryWrite
from memory_harness.time import now_ms

FACT_HALF_LIFE_MS = 30 * 24 * 3600 * 1000
PREF_HALF_LIFE_MS = 30 * 24 * 3600 * 1000
CORE_TTL_MS = 30 * 24 * 3600 * 1000
CONFIDENCE_FLOOR = 0.3
MUTABLE_FACT_TYPES = ("environment", "project")
_LAST_RUN_KEY = "decay_last_run"


def _snapshot(cur: sqlite3.Cursor, table: str, pk: str, row_id: str) -> str:
    import json

    row = cur.execute(f"SELECT * FROM {table} WHERE {pk}=?", (row_id,)).fetchone()
    return json.dumps({k: row[k] for k in row.keys()})


def _drop_from_indexes(cur: sqlite3.Cursor, kind: str, ref_id: str) -> None:
    cur.execute("DELETE FROM fts_memories WHERE ref_id=?", (ref_id,))
    if cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vec'"
    ).fetchone():
        cur.execute("DELETE FROM vec WHERE ref_id=?", (ref_id,))


async def run_decay(w: WriteQueue, *, interval_ms: int = 3600_000) -> dict[str, int]:

    def txn(cur: sqlite3.Cursor) -> dict[str, int]:
        now = now_ms()
        last = meta.get(cur, _LAST_RUN_KEY)
        last_run = int(last) if last else 0
        out = {"facts_archived": 0, "facts_decayed": 0, "prefs_archived": 0,
               "prefs_decayed": 0, "core_demoted": 0}

        for row in cur.execute(
            "SELECT * FROM facts WHERE is_head=1 AND status='active'"
            f" AND fact_type IN ({','.join('?' for _ in MUTABLE_FACT_TYPES)})",
            MUTABLE_FACT_TYPES,
        ).fetchall():
            age_periods = (now - int(row["recorded_at"])) // FACT_HALF_LIFE_MS
            applied_periods = max(0, (last_run - int(row["recorded_at"]))) // FACT_HALF_LIFE_MS
            delta = age_periods - applied_periods
            if delta <= 0:
                continue
            new_conf = float(row["confidence"]) * (0.5 ** delta)
            fid = str(row["fact_id"])
            if new_conf < CONFIDENCE_FLOOR:
                cur.execute(
                    "UPDATE facts SET status='archived', is_head=0, confidence=?"
                    " WHERE fact_id=?",
                    (new_conf, fid),
                )
                _drop_from_indexes(cur, "fact", fid)
                emit_mutation_event(
                    cur, event_type="fact_archived",
                    created_by_agent="decay-worker", created_on_node="memory-node",
                    source_trust="agent", scope=str(row["scope"]), about=[], tags=[],
                    learning_eligible=False, target_type="fact", target_id=fid,
                    emotion_id=127, content=_snapshot(cur, "facts", "fact_id", fid),
                )
                out["facts_archived"] += 1
            else:
                cur.execute(
                    "UPDATE facts SET confidence=? WHERE fact_id=?", (new_conf, fid)
                )
                out["facts_decayed"] += 1

        for row in cur.execute(
            "SELECT * FROM preferences WHERE active=1 AND strength='inferred'"
        ).fetchall():
            if now - int(row["last_confirmed_at"]) < PREF_HALF_LIFE_MS:
                continue
            new_conf = float(row["confidence"]) * 0.5
            pid = str(row["preference_id"])
            if new_conf < CONFIDENCE_FLOOR:
                cur.execute(
                    "UPDATE preferences SET active=0, confidence=? WHERE preference_id=?",
                    (new_conf, pid),
                )
                _drop_from_indexes(cur, "preference", pid)
                emit_mutation_event(
                    cur, event_type="pref_archived",
                    created_by_agent="decay-worker", created_on_node="memory-node",
                    source_trust="agent", scope=str(row["scope"]), about=[], tags=[],
                    learning_eligible=False, target_type="preference", target_id=pid,
                    emotion_id=127, content=_snapshot(cur, "preferences", "preference_id", pid),
                )
                out["prefs_archived"] += 1
            else:
                cur.execute(
                    "UPDATE preferences SET confidence=? WHERE preference_id=?",
                    (new_conf, pid),
                )
                out["prefs_decayed"] += 1

        for row in cur.execute(
            "SELECT * FROM core_memory WHERE pinned=0 AND updated_at<?",
            (now - CORE_TTL_MS,),
        ).fetchall():
            sid = str(row["slot_id"])
            cur.execute("DELETE FROM core_memory WHERE slot_id=?", (sid,))
            write_memory(
                cur,
                MemoryWrite(
                    type="note",
                    body=str(row["value_text"]),
                    scope=str(row["owner_scope"]),
                    emotion_id=127,
                    source_trust="agent",
                    created_by_agent="decay-worker",
                    created_on_node="memory-node",
                    learning_eligible=True,
                ),
            )
            emit_mutation_event(
                cur, event_type="core_edited",
                created_by_agent="decay-worker", created_on_node="memory-node",
                source_trust="agent", scope=str(row["owner_scope"]), about=[], tags=[],
                learning_eligible=False, target_type="core", target_id=sid,
                emotion_id=127, summary="decay demote",
                content='{"op": "remove", "slot_key": "decay", "slot": null, "demoted_slot_ids": []}',
            )
            out["core_demoted"] += 1

        meta.set(cur, _LAST_RUN_KEY, str(now))
        return out

    return await w.tx(txn)


async def decay_worker_loop(w: WriteQueue, *, interval_s: float = 3600.0) -> None:
    'The asyncio worker task (started in main.py lifespan).'
    from memory_harness.obs.logging import get_logger

    log = get_logger("decay_worker")
    while True:
        try:
            out = await run_decay(w)
            if any(out.values()):
                log.info("decay pass", extra=out)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("decay pass failed", extra={"error": str(e)})
        await asyncio.sleep(interval_s)
