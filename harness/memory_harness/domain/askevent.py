'The trace an ask leaves behind.'

from __future__ import annotations

import json
import sqlite3
from typing import Any

from memory_harness.domain.events import emit_mutation_event
from memory_harness.domain.secrets_scan import scan_and_redact

ASK_ANSWERED = "ask_answered"
ASK_STARTED = "ask_started"

_MAX_TEXT = 2000


def _scrub(text: str, entropy_threshold: float) -> tuple[str, bool]:
    scrubbed, had = scan_and_redact(text[:_MAX_TEXT], entropy_threshold)
    return (scrubbed or ""), had


def emit_ask_started(
    cur: sqlite3.Cursor, *, question: str, asked_by: str, node: str,
    client_ask_id: str | None, entropy_threshold: float, scope: str = "shared",
) -> str:
    q, redacted = _scrub(question, entropy_threshold)
    payload: dict[str, Any] = {"question": q, "asked_by": asked_by}
    if client_ask_id:
        payload["client_ask_id"] = client_ask_id
    return emit_mutation_event(
        cur, event_type=ASK_STARTED, created_by_agent=asked_by,
        created_on_node=node, source_trust="agent", scope=scope,
        about=[], tags=["ask"], learning_eligible=False,
        target_type=None, target_id=None, emotion_id=127,
        summary=f"asked: {q[:120]}", content=json.dumps(payload),
    )


def emit_ask_answered(
    cur: sqlite3.Cursor, *, question: str, answer: str, mood: str,
    mood_family: str, sources: list[str], tools_used: list[str],
    asked_by: str, node: str, client_ask_id: str | None, elapsed_ms: int,
    entropy_threshold: float, scope: str = "shared",
) -> str:
    q, q_red = _scrub(question, entropy_threshold)
    a, a_red = _scrub(answer, entropy_threshold)
    payload: dict[str, Any] = {
        "question": q,
        "answer": a,
        "sources": sources,
        "mood": mood,
        "moodFamily": mood_family,
        "asked_by": asked_by,
        "elapsed_ms": elapsed_ms,
    }
    if tools_used:
        payload["tools_used"] = tools_used
    if client_ask_id:
        payload["client_ask_id"] = client_ask_id

    event_id = emit_mutation_event(
        cur, event_type=ASK_ANSWERED, created_by_agent=asked_by,
        created_on_node=node, source_trust="agent", scope=scope,
        about=[], tags=["ask"], learning_eligible=False,
        target_type=None, target_id=None, emotion_id=127,
        summary=f"answered: {q[:120]}", content=json.dumps(payload),
    )
    if q_red or a_red:
        cur.execute("UPDATE events SET redacted=1 WHERE event_id=?", (event_id,))
    return event_id
