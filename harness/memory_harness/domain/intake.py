from __future__ import annotations

import sqlite3

from memory_harness.db.writer import WriteQueue
from memory_harness.domain.links import create_link, require_visible, validate_link_type
from memory_harness.domain.remember import prepare_write
from memory_harness.domain.scope import Forbidden
from memory_harness.domain.supersession import write_memory
from memory_contract import GROUNDS_LINK
from memory_harness.models.api import IntakeIn, IntakeOut
from memory_harness.models.domain import MemoryWrite, Principal


async def intake(
    w: WriteQueue,
    p: Principal,
    form: IntakeIn,
    *,
    emotion_id: int,
    entropy_threshold: float,
) -> IntakeOut:
    prep = prepare_write(
        p,
        type=form.type,
        body=form.body,
        scope=form.scope,
        source_class=form.source_class,
        confidence=form.confidence,
        entropy_threshold=entropy_threshold,
        learning_eligible=form.learning_eligible,
    )
    m = MemoryWrite(
        type=form.type,
        body=prep.body,
        scope=prep.scope,
        emotion_id=emotion_id,
        source_trust=prep.source_trust,
        created_by_agent=p.principal_id,
        created_on_node=p.node,
        learning_eligible=prep.learning_eligible,
        lineage_id=form.memory_id,
        about=form.about,
        tags=form.tags,
        confidence=prep.confidence,
        valid_from=form.valid_from,
        subject_class=form.subject_class,
        subject=form.subject,
        owner_id=p.principal_id,
        category=form.category,
        item=form.item,
    )

    def txn(cur: sqlite3.Cursor) -> IntakeOut:
        res = write_memory(cur, m)
        default_status = "active" if p.link_authority == "active" else "proposed"
        links: list[dict[str, str]] = []
        for b in form.branches:
            status = ("active" if b.link_type == GROUNDS_LINK else default_status)
            try:
                require_visible(p, cur, b.target)
            except Forbidden as e:
                raise ValueError(f"branch target not visible: {e}") from e
            src, dst = (
                (res.lineage_id, b.target) if b.direction == "out" else (b.target, res.lineage_id)
            )
            links.append(
                create_link(
                    cur,
                    src,
                    dst,
                    validate_link_type(b.link_type),
                    status,
                    proposed_by=p.principal_id,
                    curated_by=p.principal_id if status == "active" else None,
                    scope=prep.scope,
                    weight=1.0,
                    source_trust=prep.source_trust,
                    created_on_node=p.node,
                    learning_eligible=prep.learning_eligible,
                )
            )
        return IntakeOut(
            lineage_id=res.lineage_id,
            revision=res.revision,
            ref=res.ref,
            superseded=res.superseded_id,
            links=links,
        )

    return await w.tx(txn)
