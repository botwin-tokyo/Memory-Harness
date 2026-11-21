'People as nodes.'

from __future__ import annotations

import re
import sqlite3

from memory_contract import (
    KNOWS_LINK,
    person_entity_id,
    person_slug,
    person_tags,
)

from memory_harness.domain.identity import register_entity
from memory_harness.domain.links import create_link
from memory_harness.domain.supersession import write_memory
from memory_harness.models.domain import MemoryWrite, Principal

PERSON_SCOPE = "shared"

NEUTRALITY = 127


class PersonExists(ValueError):
    'A hub already exists for this person.'


def find_person(con: sqlite3.Connection | sqlite3.Cursor, name: str) -> str | None:
    'A name (any known spelling) → its `person:<slug>` id, or None.'
    key = (name or "").strip().lower()
    if not key:
        return None
    row = con.execute(
        "SELECT canonical_entity_id FROM entity_aliases"
        " WHERE alias=? AND entity_type='identity'",
        (key,),
    ).fetchone()
    if row is not None:
        eid = str(row[0])
        if eid.startswith("person:") or person_hub_lineage(con, eid) is not None:
            return eid
    slug = person_slug(name)
    eid = person_entity_id(slug)
    hit = con.execute("SELECT id FROM identities WHERE id=?", (eid,)).fetchone()
    return eid if hit is not None else None


def person_hub_lineage(
    con: sqlite3.Connection | sqlite3.Cursor, entity_id: str
) -> str | None:
    "The lineage of a person's hub node, or None if it has none yet."
    row = con.execute(
        "SELECT lineage_id FROM notes WHERE is_head=1 AND status='active'"
        " AND about LIKE ? AND tags LIKE '%\"person\"%' LIMIT 1",
        (f'%"{entity_id}"%',),
    ).fetchone()
    return str(row[0]) if row is not None else None


def hub_body(display: str, *, relation_to_companion: str = "",
             relation_to_self: str = "") -> str:
    lines = [f"[person] {display}"]
    if relation_to_companion:
        lines.append(f"to Companion: {relation_to_companion}")
    if relation_to_self:
        lines.append(f"to Assistant: {relation_to_self}")
    lines.append("")
    lines.append(
        f"Memories associated with {display}.")
    return "\n".join(lines)


ANCHORS: dict[str, str] = {"companion": "Companion", "self": "Assistant"}


def create_person(
    cur: sqlite3.Cursor,
    p: Principal,
    *,
    display: str,
    aliases: list[str] | None = None,
    relation_to_companion: str = "",
    relation_to_self: str = "",
    pronouns: str | None = None,
    entity_id: str | None = None,
) -> dict[str, str]:
    'Register the entity and mint its hub node. Idempotent per slug.'
    slug = person_slug(display)
    entity_id = entity_id or person_entity_id(slug)

    all_aliases = {display.lower(), slug, slug.replace("-", " ")}
    all_aliases |= {a.strip().lower() for a in (aliases or []) if a.strip()}
    if entity_id.startswith("person:"):
        register_entity(
            cur, "person", display, slug=slug, pronouns=pronouns,
            description=f"person: {display}",
            aliases=sorted(all_aliases),
        )

    existing = person_hub_lineage(cur, entity_id)
    m = MemoryWrite(
        type="note",
        body=hub_body(display, relation_to_companion=relation_to_companion,
                      relation_to_self=relation_to_self),
        scope=PERSON_SCOPE,
        emotion_id=NEUTRALITY,
        source_trust="user",
        created_by_agent=p.principal_id,
        created_on_node=p.node,
        learning_eligible=True,
        lineage_id=existing,
        about=[entity_id],
        tags=person_tags(slug, display=display),
        confidence=1.0,
        subject_class="person",
        owner_id=p.principal_id,
    )
    res = write_memory(cur, m)
    return {"entity_id": entity_id, "lineage_id": res.lineage_id,
            "slug": slug, "created": "false" if existing else "true"}


def link_knows(
    cur: sqlite3.Cursor,
    p: Principal,
    *,
    person_lineage: str,
    target_lineage: str,
    status: str = "active",
) -> str | None:
    '`person --knows--> someone`. Returns the link id, or None if it exists.'
    dup = cur.execute(
        "SELECT link_id FROM memory_links WHERE src_lineage_id=? AND dst_lineage_id=?"
        " AND link_type=? AND status IN ('active','proposed')",
        (person_lineage, target_lineage, KNOWS_LINK),
    ).fetchone()
    if dup is not None:
        return None
    out = create_link(
        cur, person_lineage, target_lineage, KNOWS_LINK, status,
        proposed_by=p.principal_id,
        curated_by=p.principal_id if status == "active" else None,
        scope=PERSON_SCOPE,
        source_trust="user",
        created_on_node=p.node,
    )
    return str(out.get("link_id")) if isinstance(out, dict) else None


def ensure_anchor(cur: sqlite3.Cursor, p: Principal, entity_id: str) -> str:
    row = cur.execute("SELECT display FROM identities WHERE id=?", (entity_id,)).fetchone()
    display = str(row[0]) if row else ANCHORS[entity_id]
    hub = create_person(
        cur, p, display=display, entity_id=entity_id,
    )["lineage_id"]
    other = "self" if entity_id == "companion" else "companion"
    other_hub = person_hub_lineage(cur, other)
    if other_hub is not None:
        src, dst = (hub, other_hub) if entity_id == "self" else (other_hub, hub)
        link_knows(cur, p, person_lineage=src, target_lineage=dst)
    return hub


_MEM_TABLES = (
    ("facts", "fact_id", "gloss", "status='active'"),
    ("episodes", "episode_id", "summary", "status='active'"),
    ("preferences", "preference_id", "statement", "active=1"),
    ("notes", "note_id", "body", "status='active'"),
)


def _word_pattern(names: list[str]) -> re.Pattern[str]:
    'Word-boundary alternation, **case-sensitive**.'
    alts = sorted((re.escape(n) for n in names if n), key=len, reverse=True)
    return re.compile(rf"(?<!\w)(?:{'|'.join(alts)})(?!\w)")


def _other_person_names(cur: sqlite3.Cursor) -> list[str]:
    'Display names of the people who already have hubs.'
    out: list[str] = []
    for r in cur.execute(
        "SELECT body FROM notes WHERE is_head=1 AND status='active'"
        " AND tags LIKE '%\"person\"%'"
    ).fetchall():
        head = str(r[0] or "").splitlines()[:1]
        if head and head[0].startswith("[person] "):
            out.append(head[0][len("[person] "):].strip())
    return out


def attach_existing_memories(
    cur: sqlite3.Cursor,
    p: Principal,
    *,
    entity_id: str,
    display: str,
    hub_lineage: str,
) -> int:
    'Link memories naming this person to their hub. Returns how many.'
    import json

    pat = _word_pattern([display])
    longer = [d for d in _other_person_names(cur)
              if d.lower().startswith(display.lower() + " ")]
    longer_pat = _word_pattern(longer) if longer else None
    _slug = person_slug(display)
    legacy = {display.lower(), _slug, _slug.replace("-", " ")}
    legacy.discard(entity_id)
    linked = 0
    for table, pk, col, where in _MEM_TABLES:
        for r in cur.execute(
            f"SELECT {pk} AS id, lineage_id, {col} AS s, about,"
            f" COALESCE(tags,'') AS tags FROM {table}"
            f" WHERE is_head=1 AND {where}"
        ).fetchall():
            about = json.loads(str(r["about"] or "[]"))
            by_token = [a for a in about if a in legacy]
            text = str(r["s"] or "")
            if longer_pat is not None:
                text = longer_pat.sub("", text)
            if not pat.search(text) and not by_token:
                continue
            if str(r["lineage_id"]) == hub_lineage:
                continue
            if table == "notes" and str(r["tags"] or "").find('"person"') >= 0:
                continue
            if entity_id not in about or by_token:
                fixed = [entity_id if a in legacy else a for a in about]
                if entity_id not in fixed:
                    fixed.append(entity_id)
                cur.execute(f"UPDATE {table} SET about=? WHERE {pk}=?",
                            (json.dumps(list(dict.fromkeys(fixed))), r["id"]))
            dup = cur.execute(
                "SELECT 1 FROM memory_links WHERE src_lineage_id=? AND dst_lineage_id=?"
                " AND link_type='relates_to' AND status IN ('active','proposed')",
                (r["lineage_id"], hub_lineage)).fetchone()
            if dup is None:
                create_link(cur, str(r["lineage_id"]), hub_lineage, "relates_to",
                            "active", proposed_by=p.principal_id,
                            curated_by=p.principal_id, scope=PERSON_SCOPE,
                            source_trust="user", created_on_node=p.node)
                linked += 1
    return linked


def merge_people(
    cur: sqlite3.Cursor, p: Principal, *, loser_slug: str, winner_slug: str
) -> dict[str, str]:
    'Fold one person into another. The loser is RETIRED, never deleted.'
    import json

    loser_id, winner_id = person_entity_id(loser_slug), person_entity_id(winner_slug)
    loser_hub = person_hub_lineage(cur, loser_id)
    winner_hub = person_hub_lineage(cur, winner_id)
    if loser_hub is None or winner_hub is None:
        raise ValueError(f"cannot merge {loser_slug} into {winner_slug}: hub missing")

    moved_about = moved_edges = 0
    for table, pk, _col, where in _MEM_TABLES:
        for r in cur.execute(
            f"SELECT {pk} AS id, about FROM {table} WHERE is_head=1 AND {where}"
            f" AND about LIKE ?", (f'%"{loser_id}"%',)
        ).fetchall():
            about = json.loads(str(r["about"] or "[]"))
            new = [winner_id if a == loser_id else a for a in about]
            new = list(dict.fromkeys(new))
            if new != about:
                cur.execute(f"UPDATE {table} SET about=? WHERE {pk}=?",
                            (json.dumps(new), r["id"]))
                moved_about += 1

    for col in ("src_lineage_id", "dst_lineage_id"):
        rows = cur.execute(
            f"SELECT link_id FROM memory_links WHERE {col}=? AND status='active'",
            (loser_hub,)).fetchall()
        for row in rows:
            cur.execute(f"UPDATE memory_links SET {col}=? WHERE link_id=?",
                        (winner_hub, row["link_id"]))
            moved_edges += 1

    cur.execute(
        "UPDATE OR IGNORE entity_aliases SET canonical_entity_id=?"
        " WHERE canonical_entity_id=?", (winner_id, loser_id))
    cur.execute("DELETE FROM entity_aliases WHERE canonical_entity_id=?", (loser_id,))
    cur.execute(
        "UPDATE notes SET status='retracted', is_head=0 WHERE lineage_id=?",
        (loser_hub,))
    cur.execute("DELETE FROM fts_memories WHERE ref_id IN"
                " (SELECT note_id FROM notes WHERE lineage_id=?)", (loser_hub,))
    if cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vec'"
                   ).fetchone():
        cur.execute("DELETE FROM vec WHERE ref_id IN"
                    " (SELECT note_id FROM notes WHERE lineage_id=?)", (loser_hub,))
    return {"merged": loser_slug, "into": winner_slug,
            "about_moved": str(moved_about), "edges_moved": str(moved_edges)}
