from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from memory_harness import ids
from memory_harness.content_store import ContentStore
from memory_harness.db.writer import WriteQueue
from memory_harness.domain.salience import score_event
from memory_harness.domain.secrets_scan import scan_and_redact
from memory_harness.models.api import EventIn
from memory_harness.models.domain import Principal
from memory_harness.time import now_ms
from memory_harness.workers import queue

IDEMPOTENCY_WINDOW_MS = 10 * 60 * 1000

CAPTURE_EVENT_TYPES = {
    "user_msg",
    "assistant_msg",
    "tool_call",
    "tool_result",
    "artifact",
    "external_doc",
}

ACTOR_BY_EVENT_TYPE = {
    "user_msg": "user",
    "assistant_msg": "assistant",
    "tool_call": "tool",
    "tool_result": "tool",
    "artifact": "assistant",
    "external_doc": "external",
}


@dataclass(frozen=True)
class AppendResult:
    event_id: str
    deduped: bool
    redacted: bool
    extract_enqueued: bool


def _content_hash(text: str) -> str:
    'sha256 of whitespace-normalized content (fact-level dedupe signal).'
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


async def append_event(
    w: WriteQueue,
    store: ContentStore,
    ev: EventIn,
    principal: Principal,
    *,
    scope: str,
    source_trust: str,
    emotion_id: int,
    inline_body_max_bytes: int,
    entropy_threshold: float,
) -> AppendResult:
    content, had_secret = scan_and_redact(ev.content, entropy_threshold)
    learning_eligible = principal.learning_eligible_default and not had_secret

    content_ref = ev.content_ref
    inline = content
    if content is not None and len(content.encode("utf-8")) > inline_body_max_bytes:
        content_ref = store.put(content.encode("utf-8"))
        inline = None

    event_id = ids.uuid7()
    now = now_ms()
    actor = ACTOR_BY_EVENT_TYPE[ev.event_type]
    content_hash = _content_hash(content) if content else None

    def txn(cur: sqlite3.Cursor) -> AppendResult:
        prior = cur.execute(
            "SELECT event_id, redacted FROM events"
            " WHERE idempotency_key=? AND recorded_at>?",
            (ev.idempotency_key, now - IDEMPOTENCY_WINDOW_MS),
        ).fetchone()
        if prior is not None:
            return AppendResult(
                event_id=str(prior["event_id"]),
                deduped=True,
                redacted=bool(prior["redacted"]),
                extract_enqueued=False,
            )
        salience = (
            ev.salience_hint
            if ev.salience_hint is not None
            else score_event(cur, ev)
        )
        cur.execute(
            "INSERT INTO events(event_id, occurred_at, recorded_at, session_id,"
            " thread_id, task_id, event_type, actor, created_by_agent,"
            " created_on_node, source_trust, content, content_ref, summary,"
            " about, tags, scope, content_hash, idempotency_key,"
            " learning_eligible, redacted, emotion_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                now,
                now,
                ev.session_id,
                None,
                ev.task_id,
                ev.event_type,
                actor,
                principal.principal_id,
                principal.node,
                source_trust,
                inline,
                content_ref,
                None,
                _json(ev.about),
                _json(ev.tags),
                scope,
                content_hash,
                ev.idempotency_key,
                1 if learning_eligible else 0,
                1 if had_secret else 0,
                emotion_id,
            ),
        )
        queue.enqueue(cur, "embed", {"ref_type": "event", "ref_id": event_id})
        extract_enqueued = False
        if salience == "high":
            queue.enqueue(cur, "extract", {"ref_type": "event", "ref_id": event_id})
            extract_enqueued = True
        return AppendResult(
            event_id=event_id,
            deduped=False,
            redacted=had_secret,
            extract_enqueued=extract_enqueued,
        )

    return await w.tx(txn)


def _json(items: list[str]) -> str:
    import json

    return json.dumps(items)


def emit_mutation_event(
    cur: sqlite3.Cursor,
    *,
    event_type: str,
    created_by_agent: str,
    created_on_node: str,
    source_trust: str,
    scope: str,
    about: list[str],
    tags: list[str],
    learning_eligible: bool,
    target_type: str | None,
    target_id: str | None,
    emotion_id: int,
    summary: str | None = None,
    content: str | None = None,
) -> str:
    event_id = ids.uuid7()
    now = now_ms()
    cur.execute(
        "INSERT INTO events(event_id, occurred_at, recorded_at, event_type, actor,"
        " created_by_agent, created_on_node, source_trust, content, summary, about,"
        " tags, scope, learning_eligible, target_type, target_id, emotion_id)"
        " VALUES (?,?,?,?,'system',?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            event_id,
            now,
            now,
            event_type,
            created_by_agent,
            created_on_node,
            source_trust,
            content,
            summary,
            _json(about),
            _json(tags),
            scope,
            1 if learning_eligible else 0,
            target_type,
            target_id,
            emotion_id,
        ),
    )
    return event_id
