'Propose book ↔ memory links, off hours. Never activate them.'

from __future__ import annotations

import logging
import sqlite3
import json
from collections import defaultdict
from datetime import datetime

from memory_contract import node_kind

log = logging.getLogger("memory_harness.curator.booklinks")

FLOOR = 0.18

TOP_K = 5

OFF_HOURS = range(0, 2)

_MEMORY_TABLES = (
    ("facts", "fact_id", "gloss", None),
    ("episodes", "episode_id", "summary", None),
    ("preferences", "preference_id", "statement", None),
    ("notes", "note_id", "body", "tags"),
)


def is_off_hours(now: datetime | None = None) -> bool:
    return (now or datetime.now()).hour in OFF_HOURS


def _memory_lineage(con: sqlite3.Connection, ref_id: str) -> tuple[str, str] | None:
    'A vec ref_id → (lineage_id, text), or None if it is not a real memory.'
    for table, pk, col, tagcol in _MEMORY_TABLES:
        cols = f"lineage_id, {col}" + (f", {tagcol}" if tagcol else "")
        row = con.execute(
            f"SELECT {cols} FROM {table} WHERE {pk}=? AND is_head=1", (ref_id,)
        ).fetchone()
        if row is None:
            continue
        if tagcol is not None:
            tags = json.loads(row[2] or "[]")
            if node_kind(tags) != "memory":
                return None
        return str(row[0]), str(row[1] or "")
    return None


def candidates(con: sqlite3.Connection, *, top_k: int = TOP_K,
               floor: float = FLOOR) -> dict[str, list[tuple[str, float, str]]]:
    'book hub lineage → [(memory lineage, similarity, preview)], best first.'
    hubs: dict[str, str] = {}
    for r in con.execute(
        "SELECT lineage_id, tags FROM notes WHERE is_head=1 AND status='active'"
        " AND scope='library' AND tags LIKE '%\"book\"%'"
    ):
        tags = json.loads(r["tags"] or "[]")
        slug = next((t.split(":", 1)[1] for t in tags if str(t).startswith("book:")), None)
        if slug and "book-chunk" not in tags:
            hubs[slug] = str(r["lineage_id"])

    scored: dict[str, dict[str, tuple[float, str]]] = defaultdict(dict)
    for row in con.execute("SELECT book, embedding FROM vec_library"):
        slug = str(row["book"])
        if slug not in hubs:
            continue
        for hit in con.execute(
            "SELECT ref_id, distance FROM vec WHERE embedding MATCH ? AND k=5",
            (row["embedding"],),
        ):
            sim = 1.0 - float(hit["distance"])
            if sim < floor:
                continue
            found = _memory_lineage(con, str(hit["ref_id"]))
            if found is None:
                continue
            lineage, text = found
            prev = scored[slug].get(lineage)
            if prev is None or sim > prev[0]:
                scored[slug][lineage] = (sim, text[:80])

    out: dict[str, list[tuple[str, float, str]]] = {}
    for slug, per_mem in scored.items():
        ranked = sorted(per_mem.items(), key=lambda kv: kv[1][0], reverse=True)[:top_k]
        if ranked:
            out[hubs[slug]] = [(lin, sim, prev) for lin, (sim, prev) in ranked]
    return out


def existing_pairs(con: sqlite3.Connection) -> set[tuple[str, str]]:
    '(src, dst) pairs already linked in ANY status.'
    return {
        (str(r["src_lineage_id"]), str(r["dst_lineage_id"]))
        for r in con.execute(
            "SELECT src_lineage_id, dst_lineage_id FROM memory_links")
    }


def propose(c: object, con: sqlite3.Connection, *, dry_run: bool = False,
            top_k: int = TOP_K, floor: float = FLOOR) -> dict[str, object]:
    'One pass: find candidates, propose the new ones. Never activates.'
    found = candidates(con, top_k=top_k, floor=floor)
    already = existing_pairs(con)
    proposed: list[dict[str, object]] = []
    skipped = 0

    for hub, rows in found.items():
        for lineage, sim, preview in rows:
            if (lineage, hub) in already or (hub, lineage) in already:
                skipped += 1
                continue
            proposed.append({"src": lineage, "dst": hub,
                             "similarity": round(sim, 4), "preview": preview})
            if dry_run:
                continue
            r = c.harness.post(  # type: ignore[attr-defined]
                "/v1/link",
                json={"src_lineage_id": lineage, "dst_lineage_id": hub,
                      "link_type": "relates_to", "scope": "library"},
                headers=c.headers,  # type: ignore[attr-defined]
            )
            if r.status_code != 200:
                log.warning("book link proposal refused (%s): %s",
                            r.status_code, r.text[:120])

    log.info("booklinks: %d proposed, %d already known, %d book(s) considered",
             len(proposed), skipped, len(found))
    return {"proposed": proposed, "skipped": skipped, "books": len(found),
            "dry_run": dry_run}
