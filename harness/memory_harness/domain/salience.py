from __future__ import annotations

import re
import sqlite3

from memory_harness.models.api import EventIn
from memory_harness.time import now_ms

MEMORY_VERB_RE = re.compile(r"\b(remember|always|never|from now on)\b", re.IGNORECASE)
CORRECTION_RE = re.compile(
    r"\b(actually|correction|that'?s wrong|not right|i meant|no,? wait)\b", re.IGNORECASE
)
LONG_LIVED_TASK_MS = 3_600_000


def score_event(cur: sqlite3.Cursor, e: EventIn) -> str:
    "Score one event 'low'|'high'. No model, no latency budget impact."
    content = e.content or ""
    if MEMORY_VERB_RE.search(content):
        return "high"
    if e.event_type in ("decision", "error"):
        return "high"
    if CORRECTION_RE.search(content):
        return "high"
    for entity in e.about:
        row = cur.execute(
            "SELECT 1 FROM events WHERE about LIKE ? LIMIT 1", (f'%"{entity}"%',)
        ).fetchone()
        if row is None:
            return "high"
    if e.task_id:
        row = cur.execute(
            "SELECT MIN(recorded_at) FROM events WHERE task_id=?", (e.task_id,)
        ).fetchone()
        if row is not None and row[0] is not None and now_ms() - int(row[0]) > LONG_LIVED_TASK_MS:
            return "high"
    return "low"
