from __future__ import annotations

import sqlite3


def get(cur: sqlite3.Cursor, key: str) -> str | None:
    'Return the meta value for `key`, or None if unset.'
    row = cur.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row is not None else None


def set(cur: sqlite3.Cursor, key: str, value: str) -> None:  # noqa: A001 - matches plan naming
    'Upsert a meta key.'
    cur.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
