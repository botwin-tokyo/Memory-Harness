from __future__ import annotations

import json
import sqlite3

from memory_contract import LIBRARY_SCOPE

from memory_harness.domain.scope import Forbidden
from memory_harness.models.domain import Principal

MAX_SEARCH_LIMIT = 50


def _page_of(tags: str) -> int | None:
    for tok in json.loads(tags or "[]"):
        if str(tok).startswith("page:"):
            try:
                return int(str(tok).split(":", 1)[1])
            except ValueError:
                return None
    return None


_MAX_DISTANCE = 1.05


def _relevant(distance: float) -> bool:
    'Is this passage actually about the query, or merely the closest book?'
    return distance < _MAX_DISTANCE


def list_books(con: sqlite3.Connection) -> list[dict[str, object]]:
    'Every book hub, with its page count.'
    out: list[dict[str, object]] = []
    for row in con.execute(
        "SELECT lineage_id, note_id, body, tags FROM notes"
        " WHERE is_head=1 AND status='active' AND scope=?"
        " AND tags LIKE '%\"book\"%' AND tags NOT LIKE '%book-chunk%'",
        (LIBRARY_SCOPE,),
    ):
        tags = json.loads(row["tags"] or "[]")
        slug = next((t.split(":", 1)[1] for t in tags
                     if str(t).startswith("book:")), None)
        pages = con.execute(
            "SELECT COUNT(*) FROM notes WHERE is_head=1 AND status='active'"
            " AND scope=? AND tags LIKE ?",
            (LIBRARY_SCOPE, f'%"book:{slug}"%'),
        ).fetchone()[0]
        out.append({
            "book": slug,
            "lineage_id": str(row["lineage_id"]),
            "body": str(row["body"]),
            "pages": max(0, int(pages) - 1),
        })
    return out


def search(
    con: sqlite3.Connection,
    principal: Principal,
    *,
    query: str,
    qvec: list[float] | None,
    book: str | None = None,
    limit: int = 8,
) -> dict[str, object]:
    'Passages from the library, nearest first, each citable.'
    if LIBRARY_SCOPE not in set(principal.allowed_scopes):
        raise Forbidden(LIBRARY_SCOPE)

    limit = max(1, min(int(limit), MAX_SEARCH_LIMIT))
    hits: list[dict[str, object]] = []
    if qvec is not None:
        sql = ("SELECT ref_id, book, distance FROM vec_library"
               " WHERE embedding MATCH ? AND k = ?")
        args: list[object] = [json.dumps(qvec), limit * 4]
        rows = [
            r for r in con.execute(sql, args).fetchall()
            if _relevant(float(r["distance"]))
        ]
    else:
        like = f"%{query.strip()}%"
        rows = con.execute(
            "SELECT note_id AS ref_id, '' AS book, 1.0 AS distance FROM notes"
            " WHERE is_head=1 AND status='active' AND scope=?"
            " AND tags LIKE '%book-chunk%' AND body LIKE ? LIMIT ?",
            (LIBRARY_SCOPE, like, max(1, limit) * 4),
        ).fetchall()

    for r in rows:
        if book and str(r["book"]) and str(r["book"]) != book:
            continue
        note = con.execute(
            "SELECT lineage_id, body, tags, scope FROM notes"
            " WHERE note_id=? AND is_head=1 AND status='active'",
            (str(r["ref_id"]),),
        ).fetchone()
        if note is None or str(note["scope"]) != LIBRARY_SCOPE:
            continue
        tags = json.loads(note["tags"] or "[]")
        slug = next((t.split(":", 1)[1] for t in tags
                     if str(t).startswith("book:")), str(r["book"]))
        if book and slug != book:
            continue
        hits.append({
            "book": slug,
            "page": _page_of(note["tags"]),
            "lineage_id": str(note["lineage_id"]),
            "text": str(note["body"]),
            "relevance": round(1.0 - float(r["distance"]), 4),
        })
        if len(hits) >= limit:
            break

    return {"query": query, "book": book, "passages": hits, "count": len(hits)}
