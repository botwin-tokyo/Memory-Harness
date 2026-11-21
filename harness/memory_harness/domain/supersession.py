from __future__ import annotations

import json
import sqlite3

from memory_contract import CONTACT_TAG, NO_EMBED_TAG, PREFERENCE_DEFAULT_CATEGORY, slugify_item

from memory_harness import ids
from memory_harness.domain.events import emit_mutation_event
from memory_harness.domain.identity import (
    VALID_SUBJECT_CLASSES,
    resolve_canonical,
    resolve_subject_class,
)
from memory_harness.domain.trust import tier_ge
from memory_harness.models.domain import MemoryResult, MemoryWrite
from memory_harness.time import now_ms
from memory_harness.workers import queue

TABLE_BY_TYPE = {
    "fact": "facts",
    "preference": "preferences",
    "episode": "episodes",
    "note": "notes",
    "credential": "notes",
    "email": "notes",
    "contact": "notes",
    "procedure": "procedures",
}
PK_BY_TABLE = {
    "facts": "fact_id",
    "preferences": "preference_id",
    "episodes": "episode_id",
    "notes": "note_id",
    "procedures": "procedure_id",
}
EVENT_CREATED = {
    "fact": "fact_minted",
    "preference": "pref_upserted",
    "episode": "episode_created",
    "note": "note_created",
    "credential": "note_created",
    "email": "note_created",
    "contact": "note_created",
    "procedure": "procedure_created",
}
FTS_KIND = {
    "fact": "fact",
    "preference": "preference",
    "episode": "episode",
    "note": "note",
    "credential": "note",
    "email": "note",
    "contact": "note",
    "procedure": "procedure",
}


def _subject_fields(cur: sqlite3.Cursor, m: MemoryWrite) -> tuple[str, str]:
    if m.subject is not None:
        canonical = resolve_canonical(cur, m.subject) or str(m.subject).strip().lower()
        cls = m.subject_class or resolve_subject_class(cur, canonical)
        return canonical, cls
    if m.subject_class is not None:
        if m.subject_class not in VALID_SUBJECT_CLASSES:
            raise ValueError(f"invalid subject_class: {m.subject_class}")
        ref = m.subject_ref or (m.about[0] if m.about else "world")
        return ref, m.subject_class
    ref = m.subject_ref or (m.about[0] if m.about else "world")
    return ref, resolve_subject_class(cur, ref)


def _with_subject(m: MemoryWrite, fields: tuple[str, str]) -> MemoryWrite:
    'Return `m` with the computed (subject_ref, subject_class) applied.'
    import dataclasses

    return dataclasses.replace(m, subject_ref=fields[0], subject_class=fields[1])


def write_memory(cur: sqlite3.Cursor, m: MemoryWrite) -> MemoryResult:
    if m.type not in TABLE_BY_TYPE:
        raise ValueError(f"unknown memory type: {m.type}")
    m = _with_subject(m, _subject_fields(cur, m))

    if m.lineage_id is None:
        lid = ids.uuid7()
        return _insert_revision(
            cur, m, lineage_id=lid, revision=1, is_head=True,
            parent=None, status="active", emit=EVENT_CREATED[m.type],
        )
    head = _read_head(cur, m)
    if head is None:
        return _insert_revision(
            cur, m, lineage_id=m.lineage_id, revision=1, is_head=True,
            parent=None, status="active", emit=EVENT_CREATED[m.type],
        )

    table = TABLE_BY_TYPE[m.type]
    pk = PK_BY_TABLE[table]
    if table == "notes" and (m.type == "contact" or head["note_type"] == "contact"):
        if m.type != head["note_type"]:
            raise ValueError("contact lineage type cannot be changed")
    if tier_ge(m.source_trust, str(head["source_trust"])):
        _set_row_superseded(cur, table, pk, m, head)
        return _insert_revision(
            cur, m, lineage_id=m.lineage_id, revision=int(head["revision"]) + 1,
            is_head=True, parent=str(head[pk]), status="active",
            emit=f"{_event_prefix(m.type)}_revised",
            superseded_id=str(head[pk]),
        )

    d = _read_dispute(cur, m)
    if d is not None:
        if table in ("facts", "notes"):
            cur.execute(
                f"UPDATE {table} SET dispute_count=dispute_count+1 WHERE {pk}=?",
                (str(d[pk]),),
            )
        return MemoryResult(
            row_id=str(d[pk]),
            lineage_id=m.lineage_id,
            revision=int(d["revision"]),
            is_head=False,
            status="disputed",
            disputed=True,
            superseded_id=None,
        )
    return _insert_revision(
        cur, m, lineage_id=m.lineage_id, revision=int(head["revision"]),
        is_head=False, parent=str(head[pk]), status="disputed",
        emit=f"{_event_prefix(m.type)}_disputed",
    )


def _event_prefix(t: str) -> str:
    return {"fact": "fact", "preference": "pref", "episode": "episode",
            "note": "note", "credential": "note", "email": "note", "contact": "note",
            "procedure": "procedure"}[t]


def _row_snapshot(cur: sqlite3.Cursor, table: str, pk: str, row_id: str) -> str:
    row = cur.execute(f"SELECT * FROM {table} WHERE {pk}=?", (row_id,)).fetchone()
    return json.dumps({k: row[k] for k in row.keys()})


def _read_head(cur: sqlite3.Cursor, m: MemoryWrite) -> sqlite3.Row | None:
    table = TABLE_BY_TYPE[m.type]
    return cur.execute(
        f"SELECT * FROM {table} WHERE lineage_id=? AND scope=? AND is_head=1",
        (m.lineage_id, m.scope),
    ).fetchone()


def _read_dispute(cur: sqlite3.Cursor, m: MemoryWrite) -> sqlite3.Row | None:
    'An existing disputed fork of this lineage+scope from the same trust tier.'
    table = TABLE_BY_TYPE[m.type]
    if table in ("facts", "episodes", "notes"):
        return cur.execute(
            f"SELECT * FROM {table} WHERE lineage_id=? AND scope=?"
            " AND status='disputed' AND source_trust=?",
            (m.lineage_id, m.scope, m.source_trust),
        ).fetchone()
    if table == "preferences":
        return cur.execute(
            "SELECT * FROM preferences WHERE lineage_id=? AND scope=?"
            " AND active=0 AND is_head=0 AND source_trust=?",
            (m.lineage_id, m.scope, m.source_trust),
        ).fetchone()
    return cur.execute(
        "SELECT * FROM procedures WHERE lineage_id=? AND scope=?"
        " AND is_head=0 AND source_trust=?",
        (m.lineage_id, m.scope, m.source_trust),
    ).fetchone()


def _set_row_superseded(
    cur: sqlite3.Cursor, table: str, pk: str, m: MemoryWrite, head: sqlite3.Row
) -> None:
    head_id = str(head[pk])
    if table == "facts":
        cur.execute(
            "UPDATE facts SET is_head=0, status='superseded', valid_to=? WHERE fact_id=?",
            (now_ms(), head_id),
        )
    elif table in ("episodes", "notes"):
        cur.execute(
            f"UPDATE {table} SET is_head=0, status='superseded' WHERE {pk}=?",
            (head_id,),
        )
    elif table == "preferences":
        cur.execute(
            "UPDATE preferences SET is_head=0, active=0 WHERE preference_id=?",
            (head_id,),
        )
    else:
        cur.execute("UPDATE procedures SET is_head=0 WHERE procedure_id=?", (head_id,))
    cur.execute(
        "DELETE FROM fts_memories WHERE kind=? AND ref_id=?",
        (FTS_KIND[m.type], head_id),
    )
    if cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vec'"
    ).fetchone():
        cur.execute("DELETE FROM vec WHERE ref_id=?", (head_id,))
    emit_mutation_event(
        cur,
        event_type=f"{_event_prefix(m.type)}_superseded",
        created_by_agent=m.created_by_agent,
        created_on_node=m.created_on_node,
        source_trust=m.source_trust,
        scope=m.scope,
        about=m.about,
        tags=m.tags,
        learning_eligible=m.learning_eligible,
        target_type=m.type,
        target_id=head_id,
        emotion_id=m.emotion_id,
        content=_row_snapshot(cur, table, pk, head_id),
    )


def _insert_revision(
    cur: sqlite3.Cursor,
    m: MemoryWrite,
    *,
    lineage_id: str,
    revision: int,
    is_head: bool,
    parent: str | None,
    status: str,
    emit: str,
    superseded_id: str | None = None,
) -> MemoryResult:
    rid = ids.uuid7()
    now = now_ms()
    table = TABLE_BY_TYPE[m.type]
    if table == "facts":
        cur.execute(
            "INSERT INTO facts(fact_id, subject_ref, predicate, object_json, gloss,"
            " fact_type, scope, valid_from, valid_to, recorded_at, status, confidence,"
            " source_trust, created_by_agent, about, tags, evidence_refs,"
            " learning_eligible, lineage_id, revision, parent_revision_id, is_head,"
            " emotion_id, dispute_count, subject_class) VALUES (?,?,?,?,?,?,?,?,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                m.subject_ref,
                m.predicate or "states",
                m.object_json or json.dumps({"text": m.body}),
                m.body,
                m.fact_type or "project",
                m.scope,
                m.valid_from or now,
                now,
                status,
                m.confidence if m.confidence is not None else 0.7,
                m.source_trust,
                m.created_by_agent,
                json.dumps(m.about),
                json.dumps(m.tags),
                json.dumps(m.evidence_refs),
                1 if m.learning_eligible else 0,
                lineage_id,
                revision,
                parent,
                1 if is_head else 0,
                m.emotion_id,
                1 if status == "disputed" else 0,
                m.subject_class,
            ),
        )
    elif table == "preferences":
        cur.execute(
            "INSERT INTO preferences(preference_id, owner_id, category, item,"
            " statement, scope, strength, explicitness, confidence,"
            " last_confirmed_at, source_trust, created_by_agent, about, tags,"
            " active, in_core, lineage_id, revision, parent_revision_id,"
            " is_head, emotion_id, subject_class)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?)",
            (
                rid,
                m.owner_id or m.created_by_agent,
                m.category or PREFERENCE_DEFAULT_CATEGORY,
                m.item or slugify_item(m.body),
                m.body,
                m.scope,
                m.strength or "inferred",
                m.explicitness or "inferred",
                m.confidence if m.confidence is not None else 0.7,
                now,
                m.source_trust,
                m.created_by_agent,
                json.dumps(m.about),
                json.dumps(m.tags),
                1 if (is_head and status == "active") else 0,
                lineage_id,
                revision,
                parent,
                1 if is_head else 0,
                m.emotion_id,
                m.subject_class,
            ),
        )
    elif table == "episodes":
        cur.execute(
            "INSERT INTO episodes(episode_id, episode_type, summary, scope, status,"
            " confidence, source_trust, created_by_agent, about, tags,"
            " learning_eligible, lineage_id, revision, parent_revision_id, is_head,"
            " emotion_id, subject_class) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                m.episode_type or "session",
                m.body,
                m.scope,
                status,
                m.confidence if m.confidence is not None else 0.7,
                m.source_trust,
                m.created_by_agent,
                json.dumps(m.about),
                json.dumps(m.tags),
                1 if m.learning_eligible else 0,
                lineage_id,
                revision,
                parent,
                1 if is_head else 0,
                m.emotion_id,
                m.subject_class,
            ),
        )
    elif table == "notes":
        cur.execute(
            "INSERT INTO notes(note_id, note_type, body, scope, status, confidence,"
            " source_trust, created_by_agent, about, tags, learning_eligible,"
            " recorded_at, lineage_id, revision, parent_revision_id, is_head,"
            " emotion_id, dispute_count, subject_class) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                m.type if m.type in ("credential", "email", "contact") else "note",
                m.body,
                m.scope,
                status,
                m.confidence if m.confidence is not None else 0.7,
                m.source_trust,
                m.created_by_agent,
                json.dumps(m.about),
                json.dumps(m.tags),
                1 if m.learning_eligible else 0,
                now,
                lineage_id,
                revision,
                parent,
                1 if is_head else 0,
                m.emotion_id,
                1 if status == "disputed" else 0,
                m.subject_class,
            ),
        )
    else:
        cur.execute(
            "INSERT INTO procedures(procedure_id, name, intent_tags, steps_md, confidence,"
            " scope, created_by_agent, lineage_id, revision, parent_revision_id,"
            " is_head, emotion_id, subject_class) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                m.name or m.body[:60],
                json.dumps(m.tags),
                m.body,
                m.confidence if m.confidence is not None else 0.5,
                m.scope,
                m.created_by_agent,
                lineage_id,
                revision,
                parent,
                1 if is_head else 0,
                m.emotion_id,
                m.subject_class,
            ),
        )

    indexable = is_head and NO_EMBED_TAG not in (m.tags or [])
    if m.type == "contact":
        indexable = indexable and CONTACT_TAG in m.tags
    if indexable:
        cur.execute(
            "INSERT INTO fts_memories(body, about, tags, kind, ref_id) VALUES (?,?,?,?,?)",
            (m.body, json.dumps(m.about), json.dumps(m.tags), FTS_KIND[m.type], rid),
        )
        queue.enqueue(cur, "embed", {"ref_type": FTS_KIND[m.type], "ref_id": rid})
    emit_mutation_event(
        cur,
        event_type=emit,
        created_by_agent=m.created_by_agent,
        created_on_node=m.created_on_node,
        source_trust=m.source_trust,
        scope=m.scope,
        about=m.about,
        tags=m.tags,
        learning_eligible=m.learning_eligible,
        target_type=m.type,
        target_id=rid,
        emotion_id=m.emotion_id,
        summary=m.body[:500],
        content=_row_snapshot(cur, table, PK_BY_TABLE[table], rid),
    )
    return MemoryResult(
        row_id=rid,
        lineage_id=lineage_id,
        revision=revision,
        is_head=is_head,
        status=status,
        disputed=(status == "disputed"),
        superseded_id=superseded_id,
    )
