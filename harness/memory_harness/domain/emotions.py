from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "emotions_127.json"


class UnknownEmotion(ValueError):
    'Raised when an emotion name/id does not resolve to the vocabulary.'


def resolve_emotion(con: sqlite3.Connection | sqlite3.Cursor, name_or_id: str | int) -> int:
    'Resolve an emotion name (exact, case-insensitive) or id to its emotion_id.'
    text = str(name_or_id).strip()
    if text.isdigit():
        row = con.execute(
            "SELECT emotion_id FROM emotions WHERE emotion_id = ?", (int(text),)
        ).fetchone()
    else:
        row = con.execute(
            "SELECT emotion_id FROM emotions WHERE name = ? COLLATE NOCASE", (text,)
        ).fetchone()
    if row is None:
        raise UnknownEmotion(str(name_or_id))
    return int(row[0])


def seed_emotions(con: sqlite3.Connection, data_path: str | Path | None = None) -> int:
    'Upsert all 127 emotion rows. Idempotent; returns the row count seeded.'
    path = Path(data_path) if data_path is not None else DATA_PATH
    rows = json.loads(path.read_text(encoding="utf-8"))
    for r in rows:
        con.execute(
            "INSERT INTO emotions(emotion_id, name, family, description) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(emotion_id) DO UPDATE SET"
            "  name = excluded.name, family = excluded.family,"
            "  description = excluded.description",
            (int(r["emotion_id"]), str(r["name"]), str(r["family"]), str(r["description"])),
        )
    return len(rows)
