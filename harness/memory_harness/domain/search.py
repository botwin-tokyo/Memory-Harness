from __future__ import annotations

import json
import sqlite3
from typing import Any

from memory_contract import CONTACT_TAG, NO_EMBED_TAG, EMAIL_MESSAGE_TAG, EMAIL_PASSAGE_TAG

from memory_harness.domain.scope import visible_scopes
from memory_harness.models.domain import Principal

MAX_LIMIT = 100

_TABLES = {
    "fact": ("facts", "fact_id", "gloss", "status='active'"),
    "preference": ("preferences", "preference_id", "statement", "active=1"),
    "episode": ("episodes", "episode_id", "summary", "1=1"),
    "note": ("notes", "note_id", "body", "status='active'"),
    "procedure": ("procedures", "procedure_id", "steps_md", "1=1"),
}

_KIND_BY_TYPE = {
    "fact": "fact", "preference": "preference", "episode": "episode",
    "note": "note", "credential": "note", "email": "note", "contact": "note",
    "procedure": "procedure",
}


def _excerpt(text: str, limit: int = 400) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _row_for(con: sqlite3.Connection, kind: str, ref_id: str) -> sqlite3.Row | None:
    table, pk, text_col, live = _TABLES[kind]
    extra = ", note_type" if table == "notes" else ""
    return con.execute(
        f"SELECT lineage_id, scope, tags, {text_col} AS text{extra}"
        f" FROM {table} WHERE {pk}=? AND is_head=1 AND {live}",
        (ref_id,),
    ).fetchone()


def _declared_type(kind: str, row: sqlite3.Row) -> str:
    'What the caller would call this node.'
    if kind != "note":
        return kind
    try:
        return str(row["note_type"] or "note")
    except (IndexError, KeyError):
        return "note"


def search(
    con: sqlite3.Connection,
    principal: Principal,
    *,
    query: str,
    qvec: list[float] | None,
    types: list[str] | None = None,
    tags_all: list[str] | None = None,
    tags_any: list[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    'Ranked semantic matches, filtered before the result limit.'
    limit = max(1, min(int(limit), MAX_LIMIT))
    allowed = set(visible_scopes(principal))
    want_types = set(types or [])
    want_kinds = {_KIND_BY_TYPE[t] for t in want_types if t in _KIND_BY_TYPE}
    need_all = set(tags_all or [])
    need_any = set(tags_any or [])

    if want_types and not want_kinds:
        return {"query": query, "results": [], "count": 0}

    if qvec is None:
        raise ValueError("semantic search requires an embedding")

    rows = con.execute(
        "SELECT ref_id, kind, vec_distance_L2(embedding, ?) AS distance"
        " FROM vec ORDER BY distance, ref_id", (json.dumps(qvec),)
    )

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref_id, kind, distance in rows:
        if kind not in _TABLES or (want_kinds and kind not in want_kinds):
            continue
        row = _row_for(con, kind, ref_id)
        if row is None:
            continue
        if str(row["scope"]) not in allowed:
            continue
        declared = _declared_type(kind, row)
        if want_types and declared not in want_types:
            continue
        tags = set(json.loads(row["tags"] or "[]"))
        if declared == "email" and not {EMAIL_MESSAGE_TAG, EMAIL_PASSAGE_TAG} & tags:
            continue
        if declared == "contact" and CONTACT_TAG not in tags:
            continue
        if NO_EMBED_TAG in tags:
            continue
        if need_all and not need_all <= tags:
            continue
        if need_any and not (need_any & tags):
            continue
        lineage = str(row["lineage_id"])
        if lineage in seen:
            continue
        seen.add(lineage)
        excerpt = str(row["text"])
        if declared == "contact":
            try:
                document = json.loads(excerpt)
                excerpt = str(document.get("searchText", "")) if isinstance(document, dict) else ""
            except ValueError:
                excerpt = ""
        results.append({
            "lineage_id": str(row["lineage_id"]),
            "type": declared,
            "score": round(1.0 - distance, 4),
            "text_excerpt": _excerpt(excerpt),
            "tags": sorted(tags),
        })
        if len(results) >= limit:
            break

    return {"query": query, "results": results, "count": len(results)}
