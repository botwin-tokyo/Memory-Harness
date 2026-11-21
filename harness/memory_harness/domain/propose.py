'Claims waiting for a human. Proposed facts, and the queue they share.'

from __future__ import annotations

import json
import sqlite3

from memory_harness.workers import queue
from memory_harness.domain.events import emit_mutation_event
from memory_harness.domain.scope import OPT_IN_SCOPES, visible_scopes
from memory_harness.models.domain import Principal
from memory_harness.time import now_ms
from memory_harness import ids

PROPOSED = "proposed"

class NotProposed(ValueError):
    'The fact exists but is not awaiting a decision.'


class InvalidProposal(ValueError):
    pass


class DuplicateClaim(ValueError):
    'This claim has already been proposed, or already decided.'

    def __init__(self, body: str, fact_id: str) -> None:
        super().__init__(f"already asked: {body[:120]}")
        self.fact_id = fact_id


UNSET: object = object()


def propose_fact(
    cur: sqlite3.Cursor,
    p: Principal,
    *,
    body: str,
    scope: str,
    source_lineage: str,
    source_note: str = "",
    confidence: float = 0.5,
    emotion_id: int = 127,
    subject_class: str = "companion",
    subject_ref: str = "companion",
) -> str:
    'Write a claim awaiting review. Returns its `fact_id`.'
    dup = duplicate_of(cur, body, source_lineage)
    if dup is not None:
        raise DuplicateClaim(body, dup)

    rid = ids.uuid7()
    lineage = ids.uuid7()
    now = now_ms()
    cur.execute(
        "INSERT INTO facts(fact_id, subject_ref, predicate, object_json, gloss,"
        " fact_type, scope, valid_from, valid_to, recorded_at, status, confidence,"
        " source_trust, created_by_agent, about, tags, evidence_refs,"
        " learning_eligible, lineage_id, revision, parent_revision_id, is_head,"
        " emotion_id, dispute_count, subject_class)"
        " VALUES (?,?,?,?,?,?,?,?,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            rid, subject_ref, "states",
            json.dumps({"text": body, "source_note": source_note}), body,
            "journal", scope, now, now, PROPOSED, confidence,
            "agent", p.principal_id, json.dumps([]),
            json.dumps(["proposed", "from-journal"]),
            json.dumps([source_lineage]),
            1 if p.learning_eligible_default else 0,
            lineage, 1, None, 1, emotion_id, 0, subject_class,
        ),
    )
    emit_mutation_event(
        cur, event_type="fact_proposed", created_by_agent=p.principal_id,
        created_on_node=p.node, source_trust="agent", scope=scope,
        about=[], tags=["proposed"], learning_eligible=False,
        target_type="fact", target_id=rid, emotion_id=emotion_id,
        summary=f"proposed: {body[:120]}",
        content=json.dumps({"source_lineage": source_lineage,
                            "source_note": source_note}),
    )
    return rid


def _row(cur: sqlite3.Cursor, fact_id: str) -> sqlite3.Row:
    row = cur.execute("SELECT * FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
    if row is None:
        raise NotProposed(f"no such fact: {fact_id}")
    if str(row["status"]) != PROPOSED:
        raise NotProposed(
            f"fact {fact_id} is {row['status']}, not awaiting a decision")
    return row


def promote_fact(cur: sqlite3.Cursor, p: Principal, fact_id: str) -> dict[str, str]:
    'Approve: the claim becomes a real memory, unchanged.'
    row = _row(cur, fact_id)
    cur.execute("UPDATE facts SET status='active' WHERE fact_id=?", (fact_id,))
    cur.execute(
        "INSERT INTO fts_memories(body, about, tags, kind, ref_id)"
        " VALUES (?,?,?,'fact',?)",
        (str(row["gloss"]), str(row["about"] or "[]"), str(row["tags"] or "[]"),
         fact_id),
    )
    queue.enqueue(cur, "embed", {"ref_type": "fact", "ref_id": fact_id})
    emit_mutation_event(
        cur, event_type="fact_minted", created_by_agent=p.principal_id,
        created_on_node=p.node, source_trust=str(row["source_trust"]),
        scope=str(row["scope"]), about=[], tags=["approved"],
        learning_eligible=bool(row["learning_eligible"]),
        target_type="fact", target_id=fact_id, emotion_id=int(row["emotion_id"]),
        summary=f"approved: {str(row['gloss'])[:120]}",
    )
    return {"fact_id": fact_id, "status": "active"}


def reject_fact(cur: sqlite3.Cursor, p: Principal, fact_id: str) -> dict[str, str]:
    'Decline: retired permanently, and never proposed again.'
    row = _row(cur, fact_id)
    cur.execute(
        "UPDATE facts SET status='retracted', is_head=0 WHERE fact_id=?", (fact_id,))
    emit_mutation_event(
        cur, event_type="fact_rejected", created_by_agent=p.principal_id,
        created_on_node=p.node, source_trust=str(row["source_trust"]),
        scope=str(row["scope"]), about=[], tags=["rejected"],
        learning_eligible=False, target_type="fact", target_id=fact_id,
        emotion_id=int(row["emotion_id"]),
        summary=f"rejected: {str(row['gloss'])[:120]}",
    )
    return {"fact_id": fact_id, "status": "rejected"}


def claim_key(text: object) -> str:
    'The identity of a claim, for "have I asked this before".'
    return " ".join(str(text or "").split()).casefold().rstrip(".").strip()


def _decided_glosses(
    cur: sqlite3.Cursor, source_lineage: str, fact_type: str
) -> list[sqlite3.Row]:
    'Every claim this proposal could be a repeat of, in ANY status.'
    sql = "SELECT fact_id, gloss FROM facts WHERE fact_type=?"
    args: list[object] = [fact_type]
    if source_lineage:
        sql += " OR evidence_refs LIKE ?"
        args.append(f"%{source_lineage}%")
    return list(cur.execute(sql, args))


def duplicate_of(
    cur: sqlite3.Cursor,
    body: str,
    source_lineage: str,
    *,
    fact_type: str = "journal",
) -> str | None:
    'The `fact_id` this claim repeats, or None if it is genuinely new.'
    key = claim_key(body)
    if not key:
        return None
    for r in _decided_glosses(cur, source_lineage, fact_type):
        if claim_key(r["gloss"]) == key:
            return str(r["fact_id"])
    return None


def already_seen(
    cur: sqlite3.Cursor, source_lineage: str, *, fact_type: str = "journal"
) -> set[str]:
    'Every claim already proposed OR decided, as `claim_key` keys.'
    return {
        claim_key(r["gloss"])
        for r in _decided_glosses(cur, source_lineage, fact_type)
    }


def pending(con: sqlite3.Connection, p: Principal, *, limit: int = 200) -> dict:
    scopes = visible_scopes(p)
    ph = ",".join("?" for _ in scopes)
    args = tuple(sorted(scopes))
    items: list[dict] = []

    def head_text(lineage: str) -> str | None:
        for table, col in (("facts", "gloss"), ("episodes", "summary"),
                           ("preferences", "statement"), ("notes", "body")):
            r = con.execute(
                f"SELECT {col} FROM {table} WHERE lineage_id=? AND is_head=1",
                (lineage,)).fetchone()
            if r is not None and r[0]:
                return str(r[0])
        return None

    for l in con.execute(
        f"SELECT * FROM memory_links WHERE status='proposed' AND scope IN ({ph})"
        " ORDER BY created_at DESC LIMIT ?", (*args, limit)
    ):
        src, dst = str(l["src_lineage_id"]), str(l["dst_lineage_id"])
        st, dt = head_text(src), head_text(dst)
        items.append({
            "type": "link", "id": str(l["link_id"]),
            "link_type": str(l["link_type"]), "scope": str(l["scope"]),
            "held_for_review": str(l["scope"]) in OPT_IN_SCOPES,
            "stale": st is None or dt is None,
            "proposed_by": str(l["proposed_by"] or ""),
            "created_at": int(l["created_at"] or 0),
            "src": {"lineage_id": src, "text": (st or "")[:400] or None},
            "dst": {"lineage_id": dst, "text": (dt or "")[:400] or None},
        })

    for f in con.execute(
        f"SELECT * FROM facts WHERE status='proposed' AND scope IN ({ph})"
        " ORDER BY recorded_at DESC LIMIT ?", (*args, limit)
    ):
        refs = json.loads(str(f["evidence_refs"] or "[]"))
        source = str(refs[0]) if refs else ""
        try:
            obj = json.loads(str(f["object_json"] or "{}"))
        except ValueError:
            obj = {}
        items.append({
            "type": "fact", "id": str(f["fact_id"]),
            "claim": str(f["gloss"]), "scope": str(f["scope"]),
            "confidence": float(f["confidence"]),
            "proposed_by": str(f["created_by_agent"] or ""),
            "created_at": int(f["recorded_at"] or 0),
            "source": {
                "lineage_id": source,
                "sentence": str(obj.get("source_note") or "").strip() or None,
                "entry": (head_text(source) or "")[:200] or None,
                "text": (head_text(source) or "")[:400] or None,
            },
        })

    items.extend(pending_people(con.cursor())[:limit])

    items.sort(key=lambda i: int(i.get("created_at") or 0), reverse=True)
    return {"count": len(items), "items": items}


def propose_person(
    cur: sqlite3.Cursor,
    p: Principal,
    *,
    name: str,
    sentence: str,
    relationship: str = "",
    mentions: int = 0,
) -> str | None:
    'Queue a name for review. Returns the proposal id, or None if known.'
    from memory_contract import person_slug

    from memory_harness.domain.people import find_person

    clean = (name or "").strip()
    if not clean:
        return None
    if find_person(cur, clean) is not None:
        return None
    slug = person_slug(clean)
    seen = cur.execute(
        "SELECT 1 FROM proposed_people WHERE slug=? AND kind='person'", (slug,)
    ).fetchone()
    if seen is not None:
        return None

    pid = ids.uuid7()
    cur.execute(
        "INSERT INTO proposed_people(proposal_id, kind, name, slug, sentence,"
        " relationship, mentions, status, proposed_by, created_at)"
        " VALUES (?,'person',?,?,?,?,?, 'proposed', ?, ?)",
        (pid, clean, slug, sentence[:400], relationship, mentions,
         p.principal_id, now_ms()),
    )
    emit_mutation_event(
        cur, event_type="person_proposed", created_by_agent=p.principal_id,
        created_on_node=p.node, source_trust="agent", scope="shared",
        about=[], tags=["proposed", "person"], learning_eligible=False,
        target_type=None, target_id=None, emotion_id=127,
        summary=f"proposed person: {clean}",
        content=json.dumps({"name": clean, "sentence": sentence[:400]}),
    )
    return pid


def _proposal(cur: sqlite3.Cursor, proposal_id: str) -> sqlite3.Row:
    row = cur.execute(
        "SELECT * FROM proposed_people WHERE proposal_id=?", (proposal_id,)
    ).fetchone()
    if row is None:
        raise NotProposed(f"no such proposal: {proposal_id}")
    if str(row["status"]) != PROPOSED:
        raise NotProposed(
            f"proposal {proposal_id} is {row['status']}, not awaiting a decision")
    return row


def approve_person(
    cur: sqlite3.Cursor, p: Principal, proposal_id: str
) -> dict[str, str]:
    'Create the person, or perform the merge, depending on the kind.'
    from memory_harness.domain import people as people_mod

    row = _proposal(cur, proposal_id)
    cur.execute("UPDATE proposed_people SET status='approved' WHERE proposal_id=?",
                (proposal_id,))

    if str(row["kind"]) == "person_merge":
        out = people_mod.merge_people(
            cur, p, loser_slug=str(row["slug"]), winner_slug=str(row["merge_into"]))
        return {"proposal_id": proposal_id, "status": "approved", **out}

    made = people_mod.create_person(
        cur, p, display=str(row["name"]),
        relation_to_companion=str(row["relationship"] or ""))
    people_mod.attach_existing_memories(cur, p, entity_id=made["entity_id"],
                                        display=str(row["name"]),
                                        hub_lineage=made["lineage_id"])
    companion = people_mod.ensure_anchor(cur, p, "companion")
    people_mod.link_knows(cur, p, person_lineage=made["lineage_id"],
                          target_lineage=companion)
    return {"proposal_id": proposal_id, "status": "approved", **made}


def reject_person(
    cur: sqlite3.Cursor, p: Principal, proposal_id: str
) -> dict[str, str]:
    'Decline permanently. The name is never proposed again.'
    _proposal(cur, proposal_id)
    cur.execute("UPDATE proposed_people SET status='rejected' WHERE proposal_id=?",
                (proposal_id,))
    return {"proposal_id": proposal_id, "status": "rejected"}


def person_card(cur: sqlite3.Cursor, r: sqlite3.Row) -> dict:
    'One row → the card shape `/v1/pending` emits.'
    item: dict = {
        "type": str(r["kind"]),
        "id": str(r["proposal_id"]),
        "name": str(r["name"]),
        "sentence": str(r["sentence"]) or None,
        "relationship": str(r["relationship"]) or None,
        "mentions": int(r["mentions"] or 0),
        "proposed_by": str(r["proposed_by"] or ""),
        "created_at": int(r["created_at"] or 0),
    }
    if str(r["kind"]) == "person_merge":
        from memory_contract import person_entity_id

        from memory_harness.domain.people import person_hub_lineage

        item["merge_into"] = str(r["merge_into"])
        hub = person_hub_lineage(cur, person_entity_id(str(r["merge_into"])))
        item["merge_into_name"] = (
            _display_of(cur, hub) if hub else str(r["merge_into"]))
    return item


def pending_people(cur: sqlite3.Cursor) -> list[dict]:
    'Person and merge proposals, for the shared queue.'
    have = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='proposed_people'"
    ).fetchone()
    if have is None:
        return []
    out: list[dict] = []
    for r in cur.execute(
        "SELECT * FROM proposed_people WHERE status='proposed'"
        " ORDER BY created_at DESC LIMIT 200"
    ):
        item = person_card(cur, r)
        if str(r["kind"]) == "person_merge":
            from memory_harness.domain.people import person_hub_lineage
            from memory_contract import person_entity_id

            pass
        out.append(item)
    return out


def _display_of(cur: sqlite3.Cursor, hub_lineage: str) -> str:
    "A person hub's display name, read off its body (`[person] Sample Person`)."
    row = cur.execute(
        "SELECT body FROM notes WHERE lineage_id=? AND is_head=1", (hub_lineage,)
    ).fetchone()
    if row is None:
        return ""
    head = str(row[0] or "").splitlines()[:1]
    if head and head[0].startswith("[person] "):
        return head[0][len("[person] "):].strip()
    return ""


def propose_merge(
    cur: sqlite3.Cursor,
    p: Principal,
    *,
    loser_slug: str,
    winner_slug: str,
    sentence: str = "",
) -> str | None:
    'Queue "these two hubs are one person". Returns the id, or None if moot.'
    from memory_contract import person_entity_id

    from memory_harness.domain.people import person_hub_lineage

    loser_slug, winner_slug = loser_slug.strip(), winner_slug.strip()
    if not loser_slug or not winner_slug or loser_slug == winner_slug:
        return None
    names: dict[str, str] = {}
    for slug in (loser_slug, winner_slug):
        hub = person_hub_lineage(cur, person_entity_id(slug))
        if hub is None:
            raise ValueError(f"no person hub for {slug}")
        names[slug] = _display_of(cur, hub) or slug
    seen = cur.execute(
        "SELECT 1 FROM proposed_people WHERE slug=? AND kind='person_merge'",
        (loser_slug,)).fetchone()
    if seen is not None:
        return None

    pid = ids.uuid7()
    cur.execute(
        "INSERT INTO proposed_people(proposal_id, kind, name, slug, sentence,"
        " relationship, mentions, merge_into, status, proposed_by, created_at)"
        " VALUES (?,'person_merge',?,?,?,'',0,?, 'proposed', ?, ?)",
        (pid, names[loser_slug], loser_slug, sentence[:400], winner_slug,
         p.principal_id, now_ms()),
    )
    emit_mutation_event(
        cur, event_type="person_merge_proposed", created_by_agent=p.principal_id,
        created_on_node=p.node, source_trust="agent", scope="shared",
        about=[], tags=["proposed", "person", "merge"], learning_eligible=False,
        target_type=None, target_id=None, emotion_id=127,
        summary=f"proposed merge: {loser_slug} → {winner_slug}",
        content=json.dumps({"slug": loser_slug, "merge_into": winner_slug,
                            "sentence": sentence[:400]}),
    )
    return pid


def alias_person(
    cur: sqlite3.Cursor, p: Principal, proposal_id: str, *, of: str
) -> dict[str, str]:
    '"That name is someone I already have." The third answer.'
    from memory_harness.domain import people as people_mod

    row = _proposal(cur, proposal_id)
    if str(row["kind"]) != "person":
        raise ValueError("only a person proposal can be aliased onto someone")

    target = people_mod.find_person(cur, of)
    if target is None:
        hit = cur.execute("SELECT id FROM identities WHERE id=?", (of,)).fetchone()
        target = str(hit[0]) if hit is not None else None
    if target is None:
        raise ValueError(f"no such person to alias onto: {of}")

    hub = people_mod.person_hub_lineage(cur, target)
    if hub is None:
        raise ValueError(f"{target} has no hub to attach to")

    name = str(row["name"])
    cur.execute(
        "INSERT INTO entity_aliases(alias, canonical_entity_id, entity_type,"
        " confidence, source_ref) VALUES (?, ?, 'identity', 1.0, 'person_alias')"
        " ON CONFLICT(alias, canonical_entity_id) DO NOTHING",
        (name.lower(), target),
    )
    display = _display_of(cur, hub) or target
    attached = people_mod.attach_existing_memories(
        cur, p, entity_id=target, display=name, hub_lineage=hub)

    cur.execute("UPDATE proposed_people SET status='aliased', merge_into=?"
                " WHERE proposal_id=?", (target, proposal_id))
    emit_mutation_event(
        cur, event_type="person_aliased", created_by_agent=p.principal_id,
        created_on_node=p.node, source_trust="user", scope="shared",
        about=[target], tags=["person", "alias"], learning_eligible=False,
        target_type=None, target_id=None, emotion_id=127,
        summary=f"{name} is {display}",
        content=json.dumps({"alias": name, "of": target}),
    )
    return {"proposal_id": proposal_id, "status": "aliased", "alias": name,
            "of": target, "of_name": display, "attached": str(attached)}


def edit_person_proposal(
    cur: sqlite3.Cursor,
    p: Principal,
    proposal_id: str,
    *,
    name: str | None = None,
    sentence: object = UNSET,
    relationship: object = UNSET,
) -> dict:
    'Correct a card before deciding on it. Editing is NOT approving.'
    from memory_contract import person_slug

    from memory_harness.domain.people import find_person

    row = _proposal(cur, proposal_id)
    if str(row["kind"]) != "person":
        raise ValueError("a merge proposal has no editable name — its names "
                         "come from the two hubs it would join")

    sets: list[str] = []
    args: list[object] = []

    if name is not None:
        clean = " ".join(name.split())
        if not clean:
            raise InvalidProposal("name is empty")
        if not any(ch.isalnum() for ch in clean):
            raise InvalidProposal("name is only punctuation")
        slug = person_slug(clean)
        if slug == "unknown" and clean.lower() != "unknown":
            raise InvalidProposal(
                f"{clean!r} cannot be given a distinct id — the slug builder "
                "keeps only latin letters and digits")

        existing = find_person(cur, clean)
        if existing is not None:
            raise ValueError(
                f"{clean!r} is already {existing} — alias this proposal onto "
                "them instead of renaming it")
        clash = cur.execute(
            "SELECT proposal_id, status FROM proposed_people"
            " WHERE kind='person' AND slug=? AND proposal_id<>?",
            (slug, proposal_id)).fetchone()
        if clash is not None:
            raise ValueError(
                f"another proposal for {clean!r} already exists and is "
                f"{clash['status']}")
        sets += ["name=?", "slug=?"]
        args += [clean, slug]

    if sentence is not UNSET:
        sets.append("sentence=?")
        args.append(str(sentence or "").strip()[:400])
    if relationship is not UNSET:
        sets.append("relationship=?")
        args.append(str(relationship or "").strip()[:60])

    if sets:
        cur.execute(
            f"UPDATE proposed_people SET {', '.join(sets)} WHERE proposal_id=?",
            (*args, proposal_id))
        emit_mutation_event(
            cur, event_type="person_proposal_edited",
            created_by_agent=p.principal_id, created_on_node=p.node,
            source_trust="user", scope="shared", about=[],
            tags=["proposed", "person", "edited"], learning_eligible=False,
            target_type=None, target_id=None, emotion_id=127,
            summary=f"edited proposal: {row['name']} → {name or row['name']}",
            content=json.dumps({"was": str(row["name"]), "now": name}),
        )

    fresh = cur.execute("SELECT * FROM proposed_people WHERE proposal_id=?",
                        (proposal_id,)).fetchone()
    return person_card(cur, fresh)
