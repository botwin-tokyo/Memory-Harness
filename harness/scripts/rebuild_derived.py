from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_harness.db.connection import open_reader, open_writer  # noqa: E402
from memory_harness.db.migrate import apply_migrations  # noqa: E402
from memory_harness.domain.emotions import seed_emotions  # noqa: E402
from memory_contract import NO_EMBED_TAG, CONTACT_TAG  # noqa: E402

MEMORY_TARGET_TABLES = {
    "fact": ("facts", "fact_id", "gloss"),
    "preference": ("preferences", "preference_id", "statement"),
    "episode": ("episodes", "episode_id", "summary"),
    "note": ("notes", "note_id", "body"),
    "credential": ("notes", "note_id", "body"),
    "email": ("notes", "note_id", "body"),
    "contact": ("notes", "note_id", "body"),
    "procedure": ("procedures", "procedure_id", "steps_md"),
}

VERIFY_TABLES = [
    "events",
    "facts",
    "preferences",
    "episodes",
    "notes",
    "procedures",
    "core_memory",
    "memory_links",
    "fts_memories",
]


def _upsert(cur: sqlite3.Cursor, table: str, row: dict[str, object]) -> None:
    cols = list(row.keys())
    ph = ",".join("?" for _ in cols)
    cur.execute(
        f"INSERT OR REPLACE INTO {table}({','.join(cols)}) VALUES ({ph})",
        tuple(row[c] for c in cols),
    )


def _fts_maintain(
    cur: sqlite3.Cursor, table: str, pk: str, text_col: str, ttype: str, snap: dict[str, object]
) -> None:
    ref = str(snap[pk])
    cur.execute("DELETE FROM fts_memories WHERE ref_id=?", (ref,))
    if "status" in snap:
        is_active = snap["status"] == "active"
    elif "active" in snap:
        is_active = snap["active"] == 1
    else:
        is_active = True
    tags = set(json.loads(str(snap.get("tags") or "[]")))
    indexable = NO_EMBED_TAG not in tags and (ttype != "contact" or CONTACT_TAG in tags)
    if snap.get("is_head") == 1 and is_active and indexable:
        kind = "note" if table == "notes" else ttype
        cur.execute(
            "INSERT INTO fts_memories(body, about, tags, kind, ref_id) VALUES (?,?,?,?,?)",
            (snap[text_col], snap.get("about", "[]"), snap.get("tags", "[]"), kind, ref),
        )


def _apply_retract(cur: sqlite3.Cursor, table: str, pk: str, row_id: str) -> None:
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


def _apply_effect(cur: sqlite3.Cursor, e: sqlite3.Row) -> None:
    et = str(e["event_type"])
    tt = e["target_type"]
    content = e["content"]
    if et == "core_edited" and content:
        payload = json.loads(str(content))
        if payload.get("slot") is not None:
            _upsert(cur, "core_memory", payload["slot"])
        if payload.get("op") == "remove" or payload.get("slot") is None:
            cur.execute("DELETE FROM core_memory WHERE slot_id=?", (str(e["target_id"]),))
        for sid in payload.get("demoted_slot_ids", []):
            cur.execute("DELETE FROM core_memory WHERE slot_id=?", (str(sid),))
    elif et == "forget_executed" and content:
        payload = json.loads(str(content))
        for ent in payload.get("retracted", []):
            _apply_retract(cur, str(ent["table"]), str(ent["pk"]), str(ent["row_id"]))
        for sid in payload.get("core_slots_deleted", []):
            cur.execute("DELETE FROM core_memory WHERE slot_id=?", (str(sid),))
    elif tt in MEMORY_TARGET_TABLES and content:
        table, pk, text_col = MEMORY_TARGET_TABLES[str(tt)]
        snap = json.loads(str(content))
        _upsert(cur, table, snap)
        _fts_maintain(cur, table, pk, text_col, str(tt), snap)
    elif tt == "link" and content:
        _upsert(cur, "memory_links", json.loads(str(content)))


def rebuild(src_path: str | Path, dst_path: str | Path, upto_seq: int | None = None) -> int:
    "Replay src's event log into a fresh dst DB. Returns events replayed."
    src = open_reader(src_path)
    dst_path = Path(dst_path)
    for suffix in ("", "-wal", "-shm"):
        p = Path(f"{dst_path}{suffix}")
        if p.exists():
            p.unlink()
    dst = open_writer(dst_path)
    try:
        apply_migrations(dst)
        seed_emotions(dst)
        cur = dst.cursor()
        for row in src.execute("SELECT * FROM policy").fetchall():
            _upsert(cur, "policy", {k: row[k] for k in row.keys()})
        for row in src.execute("SELECT * FROM meta").fetchall():
            _upsert(cur, "meta", {k: row[k] for k in row.keys()})
        if upto_seq is None:
            events = src.execute("SELECT * FROM events ORDER BY seq").fetchall()
        else:
            events = src.execute(
                "SELECT * FROM events WHERE seq<=? ORDER BY seq", (upto_seq,)
            ).fetchall()
        cur.execute("BEGIN IMMEDIATE")
        for e in events:
            cols = e.keys()
            cur.execute(
                f"INSERT INTO events({','.join(cols)})"
                f" VALUES ({','.join('?' for _ in cols)})",
                tuple(e[c] for c in cols),
            )
            _apply_effect(cur, e)
        dst.commit()
        return len(events)
    finally:
        dst.close()
        src.close()


def _dump(con: sqlite3.Connection, table: str) -> list[tuple[tuple[str, str], ...]]:
    if con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is None:
        return []
    rows = con.execute(f"SELECT * FROM {table}").fetchall()
    return sorted(
        tuple(sorted(((k, str(r[k])) for k in r.keys()), key=lambda kv: kv[0]))
        for r in rows
    )


def verify(src_path: str | Path, dst_path: str | Path) -> dict[str, bool]:
    'Row-for-row comparison of every derived table between src and dst.'
    src = open_reader(src_path)
    dst = open_reader(dst_path)
    try:
        return {t: _dump(src, t) == _dump(dst, t) for t in VERIFY_TABLES}
    finally:
        src.close()
        dst.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Rebuild derived stores by replaying the event log.")
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    n = rebuild(args.src, args.dst)
    print(f"replayed {n} events into {args.dst}")
    if args.verify:
        results = verify(args.src, args.dst)
        for table, ok in results.items():
            print(f"  {table}: {'identical' if ok else 'MISMATCH'}")
        if not all(results.values()):
            sys.exit(1)


if __name__ == "__main__":
    main()
