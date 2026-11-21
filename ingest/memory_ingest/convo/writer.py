'A normalized conversation → a subtree of the central graph.'

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from memory_contract import (
    NEUTRALITY_EMOTION_NAME,
    PART_OF_LINK,
    conversation_tags,
    scan_and_redact,
    turn_tags,
)

from memory_ingest.convo.model import Conversation, Turn
from memory_ingest.state import StateDB
from memory_ingest.writer import HarnessWriter

log = logging.getLogger("memory_ingest.convo.writer")

TURNS_PER_CHUNK = 6
TURN_OVERLAP = 2
MAX_TURN_CHARS = 4000


@dataclass
class ConversationProgress:
    source: str
    session_id: str
    hub_lineage: str = ""
    turns_written: int = 0
    chunks_written: int = 0
    redacted_chunks: int = 0
    skipped: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source, "session": self.session_id,
            "hub": self.hub_lineage, "turns": self.turns_written,
            "chunks": self.chunks_written, "redacted": self.redacted_chunks,
            "skipped": self.skipped, "error": self.error,
        }


class ConversationLedger:
    '`source · session` → the hub we minted, the cursor, and what we saw.'

    def __init__(self, path: str) -> None:
        self._db = StateDB(path)
        self._db._con.execute(
            "CREATE TABLE IF NOT EXISTS conversations ("
            " source TEXT NOT NULL, session_id TEXT NOT NULL,"
            " hub_lineage TEXT NOT NULL, cursor TEXT NOT NULL DEFAULT '',"
            " content_sha TEXT NOT NULL DEFAULT '',"
            " turn_count INTEGER NOT NULL DEFAULT 0,"
            " chunk_lineages TEXT NOT NULL DEFAULT '[]',"
            " PRIMARY KEY (source, session_id))"
        )
        self._db._con.commit()

    def close(self) -> None:
        self._db.close()

    def get(self, source: str, session_id: str) -> dict | None:
        row = self._db._con.execute(
            "SELECT hub_lineage, cursor, content_sha, turn_count, chunk_lineages"
            " FROM conversations WHERE source=? AND session_id=?",
            (source, session_id),
        ).fetchone()
        if row is None:
            return None
        return {"hub_lineage": row[0], "cursor": row[1], "content_sha": row[2],
                "turn_count": row[3], "chunk_lineages": json.loads(row[4])}

    def put(self, source: str, session_id: str, *, hub_lineage: str,
            cursor: str, content_sha: str, turn_count: int,
            chunk_lineages: list[str]) -> None:
        self._db._con.execute(
            "INSERT INTO conversations(source, session_id, hub_lineage, cursor,"
            " content_sha, turn_count, chunk_lineages) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(source, session_id) DO UPDATE SET"
            " hub_lineage=excluded.hub_lineage, cursor=excluded.cursor,"
            " content_sha=excluded.content_sha, turn_count=excluded.turn_count,"
            " chunk_lineages=excluded.chunk_lineages",
            (source, session_id, hub_lineage, cursor, content_sha, turn_count,
             json.dumps(chunk_lineages)),
        )
        self._db._con.commit()

    def cursors(self, source: str) -> dict[str, str]:
        return {str(sid): str(cur) for sid, cur in self._db._con.execute(
            "SELECT session_id, cursor FROM conversations WHERE source=?", (source,))}

    def sessions(self, source: str | None = None) -> list[dict]:
        sql = ("SELECT source, session_id, hub_lineage, turn_count FROM conversations"
               + (" WHERE source=?" if source else "") + " ORDER BY source, session_id")
        rows = self._db._con.execute(sql, (source,) if source else ()).fetchall()
        return [{"source": r[0], "session_id": r[1], "hub_lineage": r[2],
                 "turns": r[3]} for r in rows]


def redact_turn(turn: Turn) -> tuple[Turn, bool]:
    'A turn with any secret redacted, and whether one was found.'
    body, hit_body = scan_and_redact(turn.content)
    calls, hit_calls = scan_and_redact(turn.tool_calls)
    reasoning, hit_reason = scan_and_redact(turn.reasoning)
    clean = Turn(
        role=turn.role, content=body or "", ts=turn.ts,
        tool_name=turn.tool_name, tool_call_id=turn.tool_call_id,
        tool_calls=calls, reasoning=reasoning, source_id=turn.source_id,
    )
    return clean, bool(hit_body or hit_calls or hit_reason)


def _truncate(text: str) -> str:
    if len(text) <= MAX_TURN_CHARS:
        return text
    return text[:MAX_TURN_CHARS] + f"\n… [turn truncated at {MAX_TURN_CHARS} chars]"


@dataclass
class TurnWindow:
    'A chunk-sized run of turns, ready to write.'

    index: int
    turns: list[Turn]
    redacted: bool = False
    roles: list[str] = field(default_factory=list)

    def body(self) -> str:
        return "\n\n".join(_truncate(t.rendered()) for t in self.turns)


def window_turns(turns: list[Turn], *, size: int = TURNS_PER_CHUNK,
                 overlap: int = TURN_OVERLAP) -> list[TurnWindow]:
    'Slice turns into overlapping windows, redacting as we go.'
    clean: list[tuple[Turn, bool]] = [redact_turn(t) for t in turns
                                      if not t.is_empty]
    windows: list[TurnWindow] = []
    step = max(1, size - overlap)
    for i in range(0, len(clean), step):
        run = clean[i:i + size]
        if not run:
            break
        windows.append(TurnWindow(
            index=len(windows),
            turns=[t for t, _ in run],
            redacted=any(hit for _, hit in run),
            roles=sorted({t.role for t, _ in run}),
        ))
        if i + size >= len(clean):
            break
    return windows


class ConversationWriter:
    'Writes the hub and its turn chunks. Idempotent by ledger.'

    def __init__(self, writer: HarnessWriter, ledger: ConversationLedger,
                 scope: str) -> None:
        self.writer = writer
        self.ledger = ledger
        self.scope = scope

    def _entity(self, convo: Conversation) -> str:
        slug = f"{convo.source}-{convo.session_id}".lower()[:80]
        return self.writer.register_entity(
            "collection", f"{convo.source}:{convo.session_id}", slug,
            f"conversation: {convo.title or convo.session_id} ({convo.source})",
            aliases=[slug],
        )

    def write(self, convo: Conversation,
              progress: ConversationProgress | None = None) -> ConversationProgress:
        p = progress or ConversationProgress(convo.source, convo.session_id)
        prior = self.ledger.get(convo.source, convo.session_id)

        if prior and prior["content_sha"] == convo.content_sha and prior["cursor"]:
            p.skipped = True
            p.hub_lineage = str(prior["hub_lineage"])
            return p

        entity = self._entity(convo)
        hub = self.writer.write_parent(
            body=convo.header(),
            entity_id=entity,
            scope=self.scope,
            tags=conversation_tags(convo.source, convo.session_id,
                                   agent=convo.agent, project=convo.project),
            memory_id=prior["hub_lineage"] if prior else None,
        )
        p.hub_lineage = hub

        start_index = int(prior["turn_count"]) if prior else 0
        chunk_lineages: list[str] = list(prior["chunk_lineages"]) if prior else []

        for window in window_turns(convo.turns):
            index = start_index + window.index
            lineage = self.writer.write_chunk(
                body=window.body(),
                entity_id=entity,
                scope=self.scope,
                parent_lineage=hub,
                index=index,
                extra_tags=turn_tags(
                    "+".join(window.roles) if window.roles else "mixed", index),
            )
            chunk_lineages.append(lineage)
            p.chunks_written += 1
            if window.redacted:
                p.redacted_chunks += 1

        p.turns_written = len(convo.turns)
        self.ledger.put(
            convo.source, convo.session_id, hub_lineage=hub,
            cursor=convo.cursor, content_sha=convo.content_sha,
            turn_count=start_index + p.chunks_written,
            chunk_lineages=chunk_lineages,
        )
        log.info("conversation %s·%s → hub %s, %d chunk(s), %d redacted",
                 convo.source, convo.session_id, hub[:13], p.chunks_written,
                 p.redacted_chunks)
        return p
