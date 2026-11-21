from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import sqlite_vec

from memory_harness import ids
from memory_harness.time import now_ms

PRIVATE_TYPES = ("fact", "preference", "note", "episode")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS private_memories (
    memory_id     TEXT PRIMARY KEY,
    type          TEXT NOT NULL,
    body          TEXT NOT NULL,
    emotion_id    INTEGER NOT NULL,
    subject_class TEXT NOT NULL DEFAULT 'self',
    tags          TEXT NOT NULL DEFAULT '[]',
    about_public  TEXT NOT NULL DEFAULT '[]',
    created_at    INTEGER NOT NULL,
    created_by    TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS private_fts USING fts5(
    body, tags, memory_id UNINDEXED
);
"""


class PrivateError(ValueError):
    'The write cannot be stored, and the caller is told why.'


def private_path(main_db_path: str) -> Path:
    '`private.db` beside the main database.'
    return Path(main_db_path).parent / "private.db"


def open_private(main_db_path: str, *, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    'The private connection. Creates the file and schema on first use.'
    path = private_path(main_db_path)
    con = sqlite3.connect(str(path), timeout=busy_timeout_ms / 1000)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    try:
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
    except Exception:  # noqa: BLE001 — keyword-only is a real degrade path
        pass
    con.executescript(_SCHEMA)
    con.commit()
    return con


def ensure_vec(con: sqlite3.Connection, dim: int) -> bool:
    'The private vector table, sized to the live embedder. True if usable.'
    try:
        con.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS private_vec USING vec0("
            f"embedding float[{int(dim)}], memory_id text)")
        con.commit()
        return True
    except sqlite3.OperationalError:
        return False


def write(
    con: sqlite3.Connection,
    *,
    type: str,
    body: str,
    emotion_id: int,
    created_by: str,
    subject_class: str = "self",
    tags: list[str] | None = None,
    about_public: list[str] | None = None,
    vector: list[float] | None = None,
) -> dict[str, object]:
    'Store one private memory. Returns its id and whether it was embedded.'
    if type not in PRIVATE_TYPES:
        raise PrivateError(
            f"unknown type {type!r}; expected one of {list(PRIVATE_TYPES)}")
    if not body.strip():
        raise PrivateError("body is empty")

    mid = ids.uuid7()
    con.execute(
        "INSERT INTO private_memories(memory_id, type, body, emotion_id,"
        " subject_class, tags, about_public, created_at, created_by)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (mid, type, body, int(emotion_id), subject_class,
         json.dumps(tags or []), json.dumps(about_public or []),
         now_ms(), created_by),
    )
    con.execute(
        "INSERT INTO private_fts(body, tags, memory_id) VALUES (?,?,?)",
        (body, json.dumps(tags or []), mid))

    embedded = False
    if vector is not None and _has_vec(con):
        con.execute(
            "INSERT INTO private_vec(embedding, memory_id) VALUES (?,?)",
            (json.dumps(vector), mid))
        embedded = True
    con.commit()
    return {"memory_id": mid, "embedded": embedded}


def _has_vec(con: sqlite3.Connection) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='private_vec'"
    ).fetchone() is not None


def _row(r: sqlite3.Row) -> dict[str, object]:
    return {
        "memory_id": str(r["memory_id"]),
        "type": str(r["type"]),
        "body": str(r["body"]),
        "emotion_id": int(r["emotion_id"]),
        "subject_class": str(r["subject_class"]),
        "tags": json.loads(str(r["tags"] or "[]")),
        "about_public": json.loads(str(r["about_public"] or "[]")),
        "created_at": int(r["created_at"]),
    }


def _fts_query(text: str) -> str | None:
    'Quote every term, so a query is data and never FTS5 syntax.'
    terms = [t.replace('"', "").strip(".,?!'").lower()
             for t in text.split() if t.strip()]
    terms = [t for t in terms if t]
    return " OR ".join(f'"{t}"' for t in terms) if terms else None


def search(
    con: sqlite3.Connection,
    query: str,
    *,
    limit: int = 10,
    vector: list[float] | None = None,
) -> list[dict[str, object]]:
    limit = max(1, min(int(limit), 50))
    found: dict[str, float] = {}

    match = _fts_query(query)
    if match:
        for i, r in enumerate(con.execute(
            "SELECT memory_id FROM private_fts WHERE private_fts MATCH ?"
            " ORDER BY bm25(private_fts) LIMIT ?", (match, limit * 3))):
            found[str(r["memory_id"])] = max(
                found.get(str(r["memory_id"]), 0.0), 1.0 - i / (limit * 3))

    if vector is not None and _has_vec(con):
        for i, r in enumerate(con.execute(
            "SELECT memory_id, distance FROM private_vec"
            " WHERE embedding MATCH ? AND k = ?",
            (json.dumps(vector), limit * 3))):
            mid = str(r["memory_id"])
            found[mid] = max(found.get(mid, 0.0), 1.0 - i / (limit * 3))

    if not found:
        return []
    order = sorted(found.items(), key=lambda kv: -kv[1])[:limit]
    out: list[dict[str, object]] = []
    for mid, score in order:
        row = con.execute(
            "SELECT * FROM private_memories WHERE memory_id=?", (mid,)).fetchone()
        if row is not None:
            out.append({**_row(row), "score": round(score, 4)})
    return out


def recent(con: sqlite3.Connection, *, limit: int = 20) -> list[dict[str, object]]:
    return [_row(r) for r in con.execute(
        "SELECT * FROM private_memories ORDER BY created_at DESC LIMIT ?",
        (max(1, min(int(limit), 100)),))]


def forget(con: sqlite3.Connection, memory_id: str) -> bool:
    'Delete a private memory outright. True if one went.'
    row = con.execute(
        "SELECT 1 FROM private_memories WHERE memory_id=?", (memory_id,)).fetchone()
    if row is None:
        return False
    con.execute("DELETE FROM private_memories WHERE memory_id=?", (memory_id,))
    con.execute("DELETE FROM private_fts WHERE memory_id=?", (memory_id,))
    if _has_vec(con):
        con.execute("DELETE FROM private_vec WHERE memory_id=?", (memory_id,))
    con.commit()
    return True


def count(con: sqlite3.Connection) -> int:
    'How many private memories exist. Content is never returned here.'
    return int(con.execute("SELECT COUNT(*) FROM private_memories").fetchone()[0])
