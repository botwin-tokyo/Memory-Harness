from __future__ import annotations

from dataclasses import replace

import json
import sqlite3
from typing import NamedTuple

from memory_contract import slugify_item

from memory_harness.db.writer import WriteQueue
from memory_harness.domain.events import emit_mutation_event
from memory_harness.domain import preferences as prefs_mod
from memory_harness.domain.scope import default_write_scope
from memory_harness.domain.secrets_scan import scan_and_redact
from memory_harness.domain.supersession import _subject_fields, write_memory
from memory_harness.domain.trust import assign_source_trust
from memory_harness.models.api import RememberIn
from memory_harness.models.domain import MemoryResult, MemoryWrite, Principal
from memory_harness.time import now_ms

DEFAULT_PREF_CATEGORY = "general"


class PreparedWrite(NamedTuple):
    scope: str
    source_trust: str
    body: str
    learning_eligible: bool
    confidence: float | None


def prepare_write(
    p: Principal,
    *,
    type: str,
    body: str,
    scope: str | None,
    source_class: str,
    confidence: float | None,
    entropy_threshold: float,
    learning_eligible: bool | None = None,
) -> PreparedWrite:
    out_scope = default_write_scope(p, scope)
    source_trust = assign_source_trust(p.trust_ceiling, source_class)
    requested = learning_eligible
    if type in ("credential", "email", "contact"):
        out_body, computed = body, False
    else:
        scanned, had_secret = scan_and_redact(body, entropy_threshold)
        out_body = scanned if scanned is not None else ""
        computed = p.learning_eligible_default and not had_secret
    out_learning = False if requested is False else computed
    out_confidence = confidence
    if source_trust == "untrusted":
        out_confidence = min(out_confidence if out_confidence is not None else 0.6, 0.6)
    return PreparedWrite(out_scope, source_trust, out_body, out_learning, out_confidence)


def _snapshot(cur: sqlite3.Cursor, preference_id: str) -> str:
    row = cur.execute(
        "SELECT * FROM preferences WHERE preference_id=?", (preference_id,)
    ).fetchone()
    return json.dumps({k: row[k] for k in row.keys()})


async def remember(
    w: WriteQueue,
    p: Principal,
    r: RememberIn,
    *,
    emotion_id: int,
    entropy_threshold: float,
) -> MemoryResult:
    prep = prepare_write(
        p,
        type=r.type,
        body=r.body,
        scope=r.scope,
        source_class=r.source_class,
        confidence=r.confidence,
        entropy_threshold=entropy_threshold,
    )
    scope, source_trust, body = prep.scope, prep.source_trust, prep.body
    learning_eligible = prep.learning_eligible

    m = MemoryWrite(
        type=r.type,
        body=body,
        scope=scope,
        emotion_id=emotion_id,
        source_trust=source_trust,
        created_by_agent=p.principal_id,
        created_on_node=p.node,
        learning_eligible=learning_eligible,
        lineage_id=r.memory_id,
        about=r.about,
        tags=r.tags,
        confidence=prep.confidence,
        valid_from=r.valid_from,
        evidence_refs=r.evidence_refs,
        subject_class=r.subject_class,
        subject=r.subject,
        owner_id=p.principal_id,
        category=r.category,
        item=r.item,
    )

    def txn(cur: sqlite3.Cursor) -> MemoryResult:
        subject_ref, subject_class = _subject_fields(cur, m)
        if r.type == "preference" and r.memory_id is None:
            category = m.category or DEFAULT_PREF_CATEGORY
            item = m.item or slugify_item(body)
            existing = cur.execute(
                "SELECT * FROM preferences WHERE owner_id=? AND category=?"
                " AND item=? AND scope=? AND subject_class=?"
                " AND active=1 AND is_head=1",
                (p.principal_id, category, item, scope, subject_class),
            ).fetchone()
            if existing is not None:
                head_statement = str(existing["statement"] or "")
                facets = prefs_mod.load_facets(
                    existing["facets"] if "facets" in existing.keys() else "[]")
                action, score, _matched = prefs_mod.classify(
                    body, head_statement, facets)

                if action == "confirm":
                    cur.execute(
                        "UPDATE preferences SET evidence_count=?,"
                        " last_confirmed_at=? WHERE preference_id=?",
                        (int(existing["evidence_count"]) + 1, now_ms(),
                         existing["preference_id"]),
                    )
                    return MemoryResult(
                        row_id=str(existing["preference_id"]),
                        lineage_id=str(existing["lineage_id"]),
                        revision=int(existing["revision"]),
                        is_head=True, status="active", disputed=False,
                        superseded_id=None,
                    )

                revised = write_memory(
                    cur, replace(m, lineage_id=str(existing["lineage_id"]),
                                 body=head_statement)
                )
                cur.execute(
                    "UPDATE preferences SET evidence_count=?, last_confirmed_at=?,"
                    " facets=? WHERE preference_id=?",
                    (
                        int(existing["evidence_count"]) + 1,
                        now_ms(),
                        json.dumps([*facets, body]),
                        revised.row_id,
                    ),
                )
                return revised
        return write_memory(cur, m)

    return await w.tx(txn)
