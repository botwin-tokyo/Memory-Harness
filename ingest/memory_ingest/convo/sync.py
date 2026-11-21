"One sweep: read a source's deltas, write them, remember where we got to."

from __future__ import annotations

import logging
from pathlib import Path

from memory_ingest.convo.hermes import HermesAdapter
from memory_ingest.convo.model import Conversation, SourceAdapter
from memory_ingest.convo.openclaw import OpenClawAdapter
from memory_ingest.convo.writer import (
    ConversationLedger,
    ConversationProgress,
    ConversationWriter,
)
from memory_ingest.writer import HarnessWriter

log = logging.getLogger("memory_ingest.convo.sync")


def build_adapter(source: str, *, path: str | Path | None = None,
                  agent: str | None = None) -> SourceAdapter:
    'The adapter for a source, pointed at `path` or its own default.'
    if source == "hermes":
        return HermesAdapter(path, agent=agent)
    if source == "openclaw":
        return OpenClawAdapter(path, agent=agent)
    raise ValueError(f"unknown conversation source: {source!r}")


def sync_source(
    source: str,
    *,
    ledger: ConversationLedger,
    writer: HarnessWriter,
    scope: str,
    path: str | Path | None = None,
    agent: str | None = None,
    full: bool = False,
    adapter: SourceAdapter | None = None,
) -> dict:
    "Read what's new from `source` and mirror it into the graph."
    adapter = adapter or build_adapter(source, path=path, agent=agent)
    cursors = {} if full else ledger.cursors(source)
    convo_writer = ConversationWriter(writer, ledger, scope)

    results: list[dict] = []
    sessions = turns = chunks = redacted = skipped = 0

    for convo in adapter.iter_conversations(cursors):
        progress = ConversationProgress(convo.source, convo.session_id)
        try:
            progress = convo_writer.write(convo, progress)
        except Exception as exc:  # noqa: BLE001 - one bad session must not
            progress.error = str(exc)
            log.exception("conversation %s failed", convo.key)
        results.append(progress.as_dict())
        sessions += 1
        if progress.skipped:
            skipped += 1
        turns += progress.turns_written
        chunks += progress.chunks_written
        redacted += progress.redacted_chunks

    out = {
        "source": source,
        "sessions_seen": sessions,
        "sessions_skipped": skipped,
        "turns": turns,
        "chunks": chunks,
        "redacted_chunks": redacted,
        "failed": [r for r in results if r.get("error")],
        "sessions": results,
    }
    log.info("sync %s: %d session(s), %d new turn(s), %d chunk(s), %d redacted",
             source, sessions, turns, chunks, redacted)
    return out
