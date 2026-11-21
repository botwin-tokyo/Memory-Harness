from __future__ import annotations

import asyncio
import json
import re
import sqlite3

from memory_harness.clients.llm import LlmClient, LlmUnavailable
from memory_harness.content_store import ContentStore
from memory_harness.db.writer import WriteQueue
from memory_harness.domain.emotions import UnknownEmotion, resolve_emotion
from memory_harness.domain.salience import MEMORY_VERB_RE
from memory_harness.domain.supersession import write_memory
from memory_harness.models.domain import MemoryWrite
from memory_harness.obs import metrics
from memory_harness.obs.logging import get_logger
from memory_harness.workers import queue

_MAX_CANDIDATE_CONFIDENCE = 0.6
_SELF_RECALL_TAGS = {"self-recall", "recall-echo"}
_PROMPT_BLOB_MAX = 6000

_PROMPT = """You are a memory-extraction worker for a personal memory harness.
Read the captured event and decide what durable knowledge it contains.

Output ONLY a JSON array. Each element:
{{"type": "fact" | "preference", "body": "<one clear statement>",
  "emotion": "<exactly one name from the vocabulary below>",
  "confidence": <0.0-1.0>, "about": ["<subject>"], "tags": ["<topic>"]}}

Rules:
- Only durable knowledge: facts about the user, projects, environment,
  relationships, or stated preferences. No chit-chat, no questions, no
  one-off task chatter.
- If nothing is worth remembering, output exactly: []
- emotion must be copied EXACTLY from this vocabulary (one per candidate):
{vocab}

EVENT (type={event_type}, actor={actor}):
\"\"\"
{content}
\"\"\""""

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _parse_candidates(answer: str) -> list[dict[str, object]]:
    m = _JSON_ARRAY_RE.search(answer)
    if m is None:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [c for c in data if isinstance(c, dict) and c.get("body") and c.get("type")]


def _event_content(cur: sqlite3.Cursor, store: ContentStore, event_id: str) -> sqlite3.Row | None:
    return cur.execute(
        "SELECT * FROM events WHERE event_id=?", (event_id,)
    ).fetchone()


async def drain_extract_jobs(
    w: WriteQueue,
    store: ContentStore,
    llm: LlmClient,
    *,
    batch: int = 4,
) -> int:
    'One drain pass. Returns the number of candidate memories minted.'
    claimed = await w.tx(lambda cur: queue.claim(cur, "extract", batch))
    if not claimed:
        return 0
    minted = 0
    log = get_logger("extract_worker")
    for job in claimed:
        job_id = str(job["job_id"])
        payload = json.loads(str(job["payload_json"]))
        event_id = str(payload["ref_id"])

        ev = await w.tx(lambda cur: _event_content(cur, store, event_id))
        if ev is None:
            await w.tx(lambda cur: queue.complete(cur, job_id))
            continue
        tags = {str(t) for t in json.loads(str(ev["tags"]))}
        if tags & _SELF_RECALL_TAGS:
            await w.tx(lambda cur: queue.complete(cur, job_id))
            continue

        content = ev["content"] or ""
        if not content and ev["content_ref"] and store.has(str(ev["content_ref"])):
            content = store.get(str(ev["content_ref"])).decode("utf-8", "replace")[:_PROMPT_BLOB_MAX]
        if not content:
            await w.tx(lambda cur: queue.complete(cur, job_id))
            continue

        vocab = await w.tx(
            lambda cur: ", ".join(
                str(r[0]) for r in cur.execute("SELECT name FROM emotions ORDER BY emotion_id")
            )
        )
        prompt = _PROMPT.format(
            vocab=vocab,
            event_type=str(ev["event_type"]),
            actor=str(ev["actor"]),
            content=str(content)[:_PROMPT_BLOB_MAX],
        )
        try:
            answer = await asyncio.to_thread(
                llm.chat, [{"role": "user", "content": prompt}]
            )
        except LlmUnavailable as e:
            await w.tx(lambda cur: queue.fail(cur, job_id, str(e)))
            log.warning("model service unavailable; extract job backed off")
            continue

        candidates = _parse_candidates(answer)
        explicit = bool(MEMORY_VERB_RE.search(str(content)))

        def mint(cur: sqlite3.Cursor) -> int:
            n = 0
            for c in candidates:
                try:
                    emotion_id = resolve_emotion(cur, str(c.get("emotion", "")))
                except UnknownEmotion:
                    emotion_id = 127
                conf_raw = c.get("confidence", 0.5)
                conf = float(conf_raw) if isinstance(conf_raw, (int, float)) else 0.5
                if not explicit or str(ev["source_trust"]) == "untrusted":
                    conf = min(conf, _MAX_CANDIDATE_CONFIDENCE)
                about_raw = c.get("about") or []
                tags_raw = c.get("tags") or []
                write_memory(
                    cur,
                    MemoryWrite(
                        type=str(c["type"]),
                        body=str(c["body"]),
                        scope=str(ev["scope"]),
                        emotion_id=emotion_id,
                        source_trust=str(ev["source_trust"]),
                        created_by_agent=str(ev["created_by_agent"]),
                        created_on_node=str(ev["created_on_node"]),
                        learning_eligible=bool(ev["learning_eligible"]),
                        about=[str(a) for a in about_raw] if isinstance(about_raw, list) else [],
                        tags=[str(t) for t in tags_raw] if isinstance(tags_raw, list) else [],
                        confidence=conf,
                        evidence_refs=[event_id],
                    ),
                )
                n += 1
            queue.complete(cur, job_id)
            metrics.extract_queue_depth.set(queue.queue_depth(cur, "extract"))
            return n

        minted += await w.tx(mint)
    return minted


async def extract_worker_loop(
    w: WriteQueue,
    store: ContentStore,
    llm: LlmClient,
    *,
    interval_s: float = 2.0,
    batch: int = 4,
) -> None:
    'The asyncio worker task (started in main.py lifespan).'
    log = get_logger("extract_worker")
    while True:
        try:
            await drain_extract_jobs(w, store, llm, batch=batch)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("extract drain failed", extra={"error": str(e)})
        await asyncio.sleep(interval_s)
