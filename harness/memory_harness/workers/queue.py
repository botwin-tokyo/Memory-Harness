from __future__ import annotations

import json
import sqlite3
from typing import Any

from memory_harness import ids
from memory_harness.time import now_ms

MAX_ATTEMPTS = 5
BASE_BACKOFF_MS = 30_000


def enqueue(
    cur: sqlite3.Cursor, kind: str, payload: dict[str, Any], run_after: int = 0
) -> str:
    'Enqueue a job. Returns the job_id.'
    job_id = ids.uuid7()
    now = now_ms()
    cur.execute(
        "INSERT INTO jobs(job_id, kind, payload_json, state, attempts, run_after,"
        " created_at, updated_at) VALUES (?, ?, ?, 'pending', 0, ?, ?, ?)",
        (job_id, kind, json.dumps(payload), run_after, now, now),
    )
    return job_id


def claim(cur: sqlite3.Cursor, kind: str, n: int) -> list[sqlite3.Row]:
    'Atomically claim up to `n` pending jobs of `kind` (state -> running).'
    now = now_ms()
    cur.execute(
        "UPDATE jobs SET state='running', attempts=attempts+1, updated_at=?"
        " WHERE job_id IN ("
        "   SELECT job_id FROM jobs"
        "   WHERE state='pending' AND kind=? AND run_after<=?"
        "   ORDER BY created_at LIMIT ?"
        " ) RETURNING *",
        (now, kind, now, n),
    )
    return cur.fetchall()


def complete(cur: sqlite3.Cursor, job_id: str) -> None:
    cur.execute(
        "UPDATE jobs SET state='done', updated_at=? WHERE job_id=?",
        (now_ms(), job_id),
    )


def fail(cur: sqlite3.Cursor, job_id: str, error: str) -> None:
    "Retry with exponential backoff, or park as 'failed' after MAX_ATTEMPTS."
    row = cur.execute("SELECT attempts FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if row is None:
        return
    attempts = int(row[0])
    now = now_ms()
    if attempts >= MAX_ATTEMPTS:
        cur.execute(
            "UPDATE jobs SET state='failed', updated_at=?, last_error=? WHERE job_id=?",
            (now, error, job_id),
        )
    else:
        backoff = BASE_BACKOFF_MS * (2 ** (attempts - 1))
        cur.execute(
            "UPDATE jobs SET state='pending', run_after=?, updated_at=?, last_error=?"
            " WHERE job_id=?",
            (now + backoff, now, error, job_id),
        )


def queue_depth(cur: sqlite3.Cursor, kind: str) -> int:
    row = cur.execute(
        "SELECT COUNT(*) FROM jobs WHERE state='pending' AND kind=?", (kind,)
    ).fetchone()
    return int(row[0])
