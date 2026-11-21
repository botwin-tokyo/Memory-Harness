'Conversation turns, pushed in live, one at a time.'

from __future__ import annotations

import json
import sqlite3

from memory_contract import (
    CONVERSATION_SOURCES,
    PART_OF_LINK,
    conversation_tags,
    turn_tags,
)

from memory_harness.domain.links import create_link
from memory_harness.domain.secrets_scan import scan_and_redact
from memory_harness.domain.supersession import write_memory
from memory_harness.models.domain import MemoryWrite, Principal
from memory_harness.time import now_ms

NEUTRAL_EMOTION = 127

TURNS_PER_CHUNK = 6
TURN_OVERLAP = 2
WINDOW_STEP = TURNS_PER_CHUNK - TURN_OVERLAP

MAX_TURN_CHARS = 4000

ROLES = ("user", "assistant", "system", "tool")

IDLE_SEAL_MS = 10 * 60 * 1000


class InvalidTurn(ValueError):
    'The payload cannot be stored, and the caller is told exactly why.'


def write_conversation_note(
    cur: sqlite3.Cursor,
    p: Principal,
    *,
    body: str,
    scope: str,
    tags: list[str],
    part_of: str | None = None,
    learning_eligible: bool = True,
) -> str:
    'A hub or a window, written through the one durable-write path.'
    res = write_memory(cur, MemoryWrite(
        type="note",
        body=body,
        scope=scope,
        emotion_id=NEUTRAL_EMOTION,
        source_trust="agent",
        created_by_agent=p.principal_id,
        created_on_node=p.node,
        learning_eligible=learning_eligible,
        about=[],
        tags=tags,
        subject_class="world",
        owner_id=p.principal_id,
    ))
    if part_of:
        create_link(
            cur, res.lineage_id, part_of, PART_OF_LINK, "active",
            proposed_by=p.principal_id, curated_by=p.principal_id, scope=scope,
            weight=1.0, source_trust="agent", created_on_node=p.node,
            learning_eligible=learning_eligible,
        )
    return res.lineage_id


def _render(role: str, content: str, tool_name: str | None) -> str:
    who = f"[{role}]" if not tool_name else f"[{role}:{tool_name}]"
    body = content if len(content) <= MAX_TURN_CHARS else (
        content[:MAX_TURN_CHARS] + f"\n… [truncated, {len(content)} chars]")
    return f"{who} {body}"


def validate(source: str, session_id: str, turns: list[dict]) -> None:
    'Reject a malformed push loudly, naming the field and the value.'
    if source not in CONVERSATION_SOURCES:
        raise InvalidTurn(
            f"unknown source {source!r}; expected one of {list(CONVERSATION_SOURCES)}")
    if not session_id.strip():
        raise InvalidTurn("session_id is required")
    if not turns:
        raise InvalidTurn("turns is empty")
    seen: set[str] = set()
    for i, t in enumerate(turns):
        for field in ("id", "seq", "role", "content", "ts"):
            if t.get(field) in (None, ""):
                raise InvalidTurn(f"turns[{i}].{field} is required")
        if str(t["role"]) not in ROLES:
            raise InvalidTurn(
                f"turns[{i}].role {t['role']!r} is not one of {list(ROLES)}")
        try:
            int(t["seq"])
        except (TypeError, ValueError):
            raise InvalidTurn(f"turns[{i}].seq must be an integer") from None
        tid = str(t["id"])
        if tid in seen:
            raise InvalidTurn(f"turns[{i}].id {tid!r} is repeated in this batch")
        seen.add(tid)


def ensure_hub(
    cur: sqlite3.Cursor,
    p: Principal,
    *,
    source: str,
    session_id: str,
    agent: str | None,
    model: str | None,
    title: str | None,
    started_at: str | None,
    scope: str,
) -> str:
    "The conversation's hub lineage, created on the first turn."
    row = cur.execute(
        "SELECT hub_lineage FROM conversation_sessions WHERE source=? AND session_id=?",
        (source, session_id)).fetchone()
    if row is not None:
        return str(row[0])

    header = "\n".join([
        f"[conversation] {title or session_id}",
        f"source: {source}" + (f" · agent: {agent}" if agent else "")
        + (f" · model: {model}" if model else ""),
        f"session: {session_id}",
    ])
    hub = write_conversation_note(
        cur, p, body=header, scope=scope,
        tags=conversation_tags(source, session_id, agent=agent))
    cur.execute(
        "INSERT INTO conversation_sessions(source, session_id, hub_lineage, agent,"
        " model, title, started_at, windows_sealed, last_push_at)"
        " VALUES (?,?,?,?,?,?,?,0,?)",
        (source, session_id, hub, agent, model, title, started_at, now_ms()))
    return hub


def record_turns(
    cur: sqlite3.Cursor, source: str, session_id: str, turns: list[dict]
) -> tuple[int, int, int]:
    'Store turns, redacting at capture. Returns (accepted, duplicate, redacted).'
    accepted = duplicate = redacted = 0
    for t in turns:
        content, hit = scan_and_redact(str(t["content"]))
        cur.execute(
            "INSERT INTO conversation_turns(source, session_id, turn_id, seq, role,"
            " content, ts, tool_name, tool_call_id, redacted, window_index, received_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?)"
            " ON CONFLICT(source, session_id, turn_id) DO NOTHING",
            (source, session_id, str(t["id"]), int(t["seq"]), str(t["role"]),
             content, str(t["ts"]), t.get("tool_name"), t.get("tool_call_id"),
             1 if hit else 0, now_ms()),
        )
        if cur.rowcount:
            accepted += 1
            redacted += 1 if hit else 0
        else:
            duplicate += 1
    return accepted, duplicate, redacted


def _unsealed(cur: sqlite3.Cursor, source: str, session_id: str) -> list[sqlite3.Row]:
    return cur.execute(
        "SELECT * FROM conversation_turns WHERE source=? AND session_id=?"
        " AND window_index IS NULL ORDER BY seq, received_at",
        (source, session_id)).fetchall()


def seal_windows(
    cur: sqlite3.Cursor,
    p: Principal,
    *,
    source: str,
    session_id: str,
    hub: str,
    scope: str,
    force: bool = False,
) -> int:
    'Write every window whose turns have all arrived. Returns how many.'
    sealed = 0
    while True:
        pending = _unsealed(cur, source, session_id)
        if not pending:
            break
        have = len(pending)
        if have < TURNS_PER_CHUNK and not force:
            break
        run = pending[:TURNS_PER_CHUNK]
        row = cur.execute(
            "SELECT windows_sealed FROM conversation_sessions"
            " WHERE source=? AND session_id=?", (source, session_id)).fetchone()
        index = int(row[0]) if row else 0

        body = "\n\n".join(
            _render(str(t["role"]), str(t["content"]), t["tool_name"]) for t in run)
        roles = "+".join(sorted({str(t["role"]) for t in run}))
        lineage = write_conversation_note(
            cur, p, body=body, scope=scope,
            tags=turn_tags(roles or "mixed", index) + [f"chunk:{index}"],
            part_of=hub,
            learning_eligible=not any(int(t["redacted"]) for t in run),
        )
        assert lineage

        consumed = run if (force and have < TURNS_PER_CHUNK) else run[:WINDOW_STEP]
        cur.executemany(
            "UPDATE conversation_turns SET window_index=? WHERE source=? AND"
            " session_id=? AND turn_id=?",
            [(index, source, session_id, str(t["turn_id"])) for t in consumed])
        cur.execute(
            "UPDATE conversation_sessions SET windows_sealed=?, last_push_at=?"
            " WHERE source=? AND session_id=?",
            (index + 1, now_ms(), source, session_id))
        sealed += 1
        if force and have < TURNS_PER_CHUNK:
            break
    return sealed


def idle_sessions(cur: sqlite3.Cursor, *, older_than_ms: int = IDLE_SEAL_MS) -> list[dict]:
    'Sessions with turns waiting and nothing new for a while.'
    cutoff = now_ms() - older_than_ms
    return [
        {"source": str(r[0]), "session_id": str(r[1]), "hub_lineage": str(r[2])}
        for r in cur.execute(
            "SELECT s.source, s.session_id, s.hub_lineage FROM conversation_sessions s"
            " WHERE s.last_push_at < ? AND EXISTS (SELECT 1 FROM conversation_turns t"
            " WHERE t.source=s.source AND t.session_id=s.session_id"
            " AND t.window_index IS NULL)", (cutoff,))
    ]


def session_state(cur: sqlite3.Cursor, source: str, session_id: str) -> dict:
    row = cur.execute(
        "SELECT hub_lineage, windows_sealed FROM conversation_sessions"
        " WHERE source=? AND session_id=?", (source, session_id)).fetchone()
    pending = cur.execute(
        "SELECT COUNT(*) FROM conversation_turns WHERE source=? AND session_id=?"
        " AND window_index IS NULL", (source, session_id)).fetchone()[0]
    total = cur.execute(
        "SELECT COUNT(*) FROM conversation_turns WHERE source=? AND session_id=?",
        (source, session_id)).fetchone()[0]
    return {
        "hub": str(row[0]) if row else None,
        "windows_sealed": int(row[1]) if row else 0,
        "turns_total": int(total),
        "turns_awaiting_window": int(pending),
    }


def push(cur: sqlite3.Cursor, p: Principal, body: dict, *, scope: str) -> dict:
    'One push: validate, record, seal what can be sealed. Returns the receipt.'
    source = str(body.get("source") or "")
    session_id = str(body.get("session_id") or "")
    turns = list(body.get("turns") or [])
    validate(source, session_id, turns)

    hub = ensure_hub(
        cur, p, source=source, session_id=session_id,
        agent=body.get("agent"), model=body.get("model"), title=body.get("title"),
        started_at=body.get("started_at"), scope=scope)
    accepted, duplicate, redacted = record_turns(cur, source, session_id, turns)
    cur.execute(
        "UPDATE conversation_sessions SET last_push_at=? WHERE source=? AND session_id=?",
        (now_ms(), source, session_id))
    windows = seal_windows(
        cur, p, source=source, session_id=session_id, hub=hub, scope=scope)
    return {
        "session_id": session_id,
        "accepted": accepted,
        "duplicate": duplicate,
        "redacted": redacted,
        "windows": windows,
        "hub": hub,
    }


def seal_idle(cur: sqlite3.Cursor, p: Principal, *, scope: str,
              older_than_ms: int = IDLE_SEAL_MS) -> dict:
    'Seal short windows on sessions that have gone quiet.'
    out = []
    for s in idle_sessions(cur, older_than_ms=older_than_ms):
        n = seal_windows(cur, p, source=s["source"], session_id=s["session_id"],
                         hub=s["hub_lineage"], scope=scope, force=True)
        if n:
            out.append({**s, "windows": n})
    return {"sealed_sessions": len(out), "sessions": out}


def sessions(cur: sqlite3.Cursor, source: str | None = None) -> list[dict]:
    'Every conversation the PUSH path holds.'
    sql = ("SELECT s.source, s.session_id, s.hub_lineage, s.agent, s.model,"
           " s.title, s.started_at, s.windows_sealed, s.last_push_at,"
           " (SELECT COUNT(*) FROM conversation_turns t WHERE t.source=s.source"
           "  AND t.session_id=s.session_id) AS turns"
           " FROM conversation_sessions s")
    args: tuple[object, ...] = ()
    if source:
        sql += " WHERE s.source=?"
        args = (source,)
    sql += " ORDER BY s.last_push_at DESC"
    return [
        {"source": str(r["source"]), "session_id": str(r["session_id"]),
         "hub_lineage": str(r["hub_lineage"]), "agent": r["agent"],
         "model": r["model"], "title": r["title"],
         "turns": int(r["turns"]), "windows": int(r["windows_sealed"]),
         "last_push_at": int(r["last_push_at"]), "via": "push"}
        for r in cur.execute(sql, args)
    ]


def transcript(cur: sqlite3.Cursor, source: str, session_id: str) -> dict | None:
    'One conversation, read by walking the graph rather than guessing at it.'
    row = cur.execute(
        "SELECT * FROM conversation_sessions WHERE source=? AND session_id=?",
        (source, session_id)).fetchone()
    if row is None:
        return None
    hub = str(row["hub_lineage"])
    header = cur.execute(
        "SELECT body FROM notes WHERE lineage_id=? AND is_head=1 AND status='active'",
        (hub,)).fetchone()

    windows = []
    linked = cur.execute(
        "SELECT src_lineage_id FROM memory_links WHERE dst_lineage_id=?"
        " AND link_type='part_of' AND status='active'", (hub,)).fetchall()
    for l in linked:
        n = cur.execute(
            "SELECT body, tags FROM notes WHERE lineage_id=? AND is_head=1"
            " AND status='active'", (str(l["src_lineage_id"]),)).fetchone()
        if n is None:
            continue
        idx = 0
        for t in json.loads(str(n["tags"] or "[]")):
            if str(t).startswith("chunk:"):
                try:
                    idx = int(str(t).split(":", 1)[1])
                except ValueError:
                    idx = 0
        windows.append((idx, str(n["body"]), str(l["src_lineage_id"])))
    windows.sort(key=lambda w: w[0])

    return {
        "source": source, "session_id": session_id, "hub_lineage": hub,
        "agent": row["agent"], "model": row["model"], "title": row["title"],
        "header": str(header["body"]) if header else None,
        "turns_total": int(cur.execute(
            "SELECT COUNT(*) FROM conversation_turns WHERE source=? AND session_id=?",
            (source, session_id)).fetchone()[0]),
        "windows": [{"index": i, "lineage_id": lid, "text": b}
                    for i, b, lid in windows],
    }
