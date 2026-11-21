from __future__ import annotations

import asyncio
import json
import sqlite3

from memory_harness.clients.embeddings import EmbeddingsClient, EmbeddingsUnavailable
from memory_contract import CONTACT_TAG, LIBRARY_SCOPE, NO_EMBED_TAG, EMAIL_MESSAGE_TAG, EMAIL_PASSAGE_TAG

from memory_harness.content_store import ContentStore
from memory_harness.db.writer import WriteQueue
from memory_harness.obs import metrics
from memory_harness.obs.logging import get_logger
from memory_harness.workers import queue

_BLOB_TEXT_MAX = 6000

_MEM_REFS = {
    "fact": ("facts", "fact_id", "gloss"),
    "preference": ("preferences", "preference_id", "statement"),
    "episode": ("episodes", "episode_id", "summary"),
    "note": ("notes", "note_id", "body"),
    "procedure": ("procedures", "procedure_id", "steps_md"),
}


def _resolve_text(
    cur: sqlite3.Cursor, store: ContentStore, ref_type: str, ref_id: str
) -> str | None:
    if ref_type == "event":
        row = cur.execute(
            "SELECT summary, content, content_ref FROM events WHERE event_id=?",
            (ref_id,),
        ).fetchone()
        if row is None:
            return None
        if row["summary"]:
            return str(row["summary"])
        if row["content"]:
            return str(row["content"])
        if row["content_ref"] and store.has(str(row["content_ref"])):
            return store.get(str(row["content_ref"])).decode("utf-8", "replace")[:_BLOB_TEXT_MAX]
        return None
    ref = _MEM_REFS.get(ref_type)
    if ref is None:
        return None
    table, pk, text_col = ref
    row = cur.execute(f"SELECT {text_col} AS t, is_head FROM {table} WHERE {pk}=?", (ref_id,)).fetchone()
    if row is None or not row["is_head"]:
        return None
    if ref_type == "note":
        meta = cur.execute("SELECT note_type, tags FROM notes WHERE note_id=?", (ref_id,)).fetchone()
        tags = set(json.loads(meta["tags"] or "[]"))
        if NO_EMBED_TAG in tags:
            return None
        if meta["note_type"] == "contact":
            if CONTACT_TAG not in tags:
                return None
            try:
                document = json.loads(row["t"])
            except (ValueError, TypeError):
                return None
            text = document.get("searchText") if isinstance(document, dict) else None
            return text if isinstance(text, str) and text else None
        if meta["note_type"] == "email":
            if not {EMAIL_MESSAGE_TAG, EMAIL_PASSAGE_TAG} & tags:
                return None
            try:
                document = json.loads(row["t"])
            except (ValueError, TypeError):
                return None
            if not isinstance(document, dict):
                return None
            if EMAIL_PASSAGE_TAG in tags:
                fields = [document.get("header", ""), document.get("text", "")]
            else:
                fields = [document.get("subject", ""),
                          json.dumps(document.get("from", {}), ensure_ascii=False),
                          json.dumps(document.get("to", []), ensure_ascii=False),
                          document.get("text", "")]
            return "\n".join(str(v) for v in fields if v)[:_BLOB_TEXT_MAX] or None
    return str(row["t"]) if row["t"] else None


def _contact_chunks(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + 1500])
        if start + 1500 >= len(text):
            break
        start += 1350
    return chunks


def _still_head(cur: sqlite3.Cursor, ref_type: str, ref_id: str) -> bool:
    'Is this ref STILL the head revision, right now?'
    ref = _MEM_REFS.get(ref_type)
    if ref is None:
        return True
    table, pk, _text_col = ref
    row = cur.execute(
        f"SELECT is_head FROM {table} WHERE {pk}=?", (ref_id,)).fetchone()
    return bool(row and row["is_head"])


def _library_book(cur: sqlite3.Cursor, ref_type: str, ref_id: str) -> str | None:
    'The book slug if this ref is a library page chunk, else None.'
    if ref_type != "note":
        return None
    row = cur.execute(
        "SELECT scope, tags FROM notes WHERE note_id=?", (ref_id,)).fetchone()
    if row is None or str(row["scope"]) != LIBRARY_SCOPE:
        return None
    for tok in json.loads(row["tags"] or "[]"):
        if str(tok).startswith("book:"):
            return str(tok).split(":", 1)[1]
    return "unknown"


def _vec_table_exists(cur: sqlite3.Cursor, name: str = "vec") -> bool:
    return (
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


async def drain_embed_jobs(
    w: WriteQueue,
    store: ContentStore,
    client: EmbeddingsClient,
    *,
    batch: int = 32,
) -> int:
    'One drain pass. Returns the number of vec rows written.'
    claimed = await w.tx(
        lambda cur: queue.claim(cur, "embed", batch) if _vec_table_exists(cur) else []
    )
    if not claimed:
        return 0

    def resolve(cur: sqlite3.Cursor) -> list[dict[str, str | None]]:
        out: list[dict[str, str | None]] = []
        for j in claimed:
            payload = json.loads(str(j["payload_json"]))
            ref_type, ref_id = str(payload["ref_type"]), str(payload["ref_id"])
            out.append(
                {
                    "job_id": str(j["job_id"]),
                    "ref_type": ref_type,
                    "ref_id": ref_id,
                    "text": _resolve_text(cur, store, ref_type, ref_id),
                }
            )
        return out

    resolved = await w.tx(resolve)
    def contact_refs(cur: sqlite3.Cursor) -> set[str]:
        return {str(r["ref_id"]) for r in resolved if r["ref_type"] == "note"
                and cur.execute("SELECT 1 FROM notes WHERE note_id=? AND note_type='contact'",
                                (r["ref_id"],)).fetchone()}

    contacts = await w.tx(contact_refs)
    unique = {(r["ref_type"], r["ref_id"]): r for r in resolved if r["text"] is not None}
    to_embed: list[dict[str, str | None]] = []
    texts: list[str] = []
    for r in unique.values():
        text = str(r["text"])
        chunks = _contact_chunks(text) if str(r["ref_id"]) in contacts else [text]
        for chunk in chunks:
            to_embed.append(r)
            texts.append(chunk)
    try:
        vectors = await asyncio.to_thread(client.embed_documents, texts)
    except EmbeddingsUnavailable as e:
        def back_off(cur: sqlite3.Cursor) -> None:
            for j in claimed:
                queue.fail(cur, str(j["job_id"]), str(e))

        await w.tx(back_off)
        get_logger("embed_worker").warning("embedding service unavailable; jobs backed off")
        return 0

    def write_rows(cur: sqlite3.Cursor) -> int:
        n = 0
        has_library = _vec_table_exists(cur, "vec_library")
        cleared: set[tuple[str, str]] = set()
        for r, vec in zip(to_embed, vectors):
            ref_type, ref_id = str(r["ref_type"]), str(r["ref_id"])
            book = _library_book(cur, ref_type, ref_id) if has_library else None
            if book is not None:
                cur.execute("DELETE FROM vec WHERE kind=? AND ref_id=?",
                            (ref_type, ref_id))
                cur.execute("DELETE FROM vec_library WHERE ref_id=?", (ref_id,))
                cur.execute(
                    "INSERT INTO vec_library(embedding, book, ref_id) VALUES (?,?,?)",
                    (json.dumps(vec), book, ref_id))
                n += 1
                continue
            key = (ref_type, ref_id)
            if key not in cleared:
                cur.execute("DELETE FROM vec WHERE kind=? AND ref_id=?", key)
                cleared.add(key)
            if not _still_head(cur, ref_type, ref_id):
                continue
            cur.execute(
                "INSERT INTO vec(embedding, kind, ref_id) VALUES (?, ?, ?)",
                (json.dumps(vec), ref_type, ref_id),
            )
            n += 1
        for j in claimed:
            queue.complete(cur, str(j["job_id"]))
        metrics.embed_queue_depth.set(queue.queue_depth(cur, "embed"))
        return n

    return await w.tx(write_rows)


async def embed_worker_loop(
    w: WriteQueue,
    store: ContentStore,
    client: EmbeddingsClient,
    *,
    interval_s: float = 1.0,
    batch: int = 32,
) -> None:
    'The asyncio worker task (started in main.py lifespan).'
    log = get_logger("embed_worker")
    while True:
        try:
            await drain_embed_jobs(w, store, client, batch=batch)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("drain failed", extra={"error": str(e)})
        await asyncio.sleep(interval_s)
