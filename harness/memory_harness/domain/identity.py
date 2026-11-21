from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "identities.json"

VALID_SUBJECT_CLASSES = ("self", "companion", "person", "world")

_PERSPECTIVE = {
    "self": "first_person",
    "companion": "second_person",
    "person": "third_person",
    "world": "neutral",
}


def perspective_of(subject_class: str) -> str:
    'The voice hint for a class: how a consuming agent should speak.'
    return _PERSPECTIVE.get(subject_class, "neutral")


def seed_identities(con: sqlite3.Connection, data_path: str | Path | None = None) -> int:
    'Upsert identities + their aliases. Idempotent; returns row count.'
    path = Path(data_path) if data_path is not None else DATA_PATH
    rows = json.loads(path.read_text(encoding="utf-8"))
    for r in rows:
        con.execute(
            "INSERT INTO identities(id, display, subject_class, pronouns, description)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET"
            "  display=excluded.display, subject_class=excluded.subject_class,"
            "  pronouns=excluded.pronouns, description=excluded.description",
            (
                str(r["id"]),
                str(r["display"]),
                str(r["subject_class"]),
                r.get("pronouns"),
                r.get("description"),
            ),
        )
        for alias in r.get("aliases", []):
            con.execute(
                "INSERT INTO entity_aliases(alias, canonical_entity_id, entity_type,"
                " confidence, source_ref) VALUES (?, ?, 'identity', 1.0, 'seed_identities')"
                " ON CONFLICT(alias, canonical_entity_id) DO NOTHING",
                (str(alias).lower(), str(r["id"])),
            )
    return len(rows)


def resolve_canonical(
    con: sqlite3.Connection | sqlite3.Cursor, subject_ref: str | None
) -> str | None:
    if not subject_ref:
        return None
    s = subject_ref.strip().lower()
    if not s:
        return None
    row = con.execute(
        "SELECT canonical_entity_id FROM entity_aliases"
        " WHERE alias=? AND entity_type='identity'",
        (s,),
    ).fetchone()
    return str(row[0]) if row is not None else s


def resolve_subject_class(
    con: sqlite3.Connection | sqlite3.Cursor, subject_ref: str | None
) -> str:
    canonical = resolve_canonical(con, subject_ref)
    if canonical is None:
        return "world"
    if canonical == "self":
        return "self"
    if canonical == "companion":
        return "companion"
    if canonical.startswith("person:"):
        return "person"
    return "world"


def identity_for_class(
    con: sqlite3.Connection | sqlite3.Cursor, subject_class: str
) -> dict[str, str | None] | None:
    'The identities row (display, pronouns) for a class, if one exists.'
    if subject_class not in ("self", "companion"):
        return None
    row = con.execute(
        "SELECT id, display, subject_class, pronouns, description"
        " FROM identities WHERE id=?",
        (subject_class,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "display": str(row["display"]),
        "subject_class": str(row["subject_class"]),
        "pronouns": None if row["pronouns"] is None else str(row["pronouns"]),
        "description": None if row["description"] is None else str(row["description"]),
    }


ENTITY_KINDS = (
    "person",
    "module",
    "repo",
    "document",
    "manual",
    "collection",
    "topic",
    "artifact",
)


def register_entity(
    con: sqlite3.Connection | sqlite3.Cursor,
    kind: str,
    name: str,
    *,
    slug: str | None = None,
    pronouns: str | None = None,
    description: str | None = None,
    aliases: list[str] | None = None,
) -> str:
    if kind not in ENTITY_KINDS:
        raise ValueError(f"unknown entity kind: {kind}")
    display = name.strip()
    if not display:
        raise ValueError("name is required")
    slug = (slug or display).strip().lower().replace(" ", "-")
    eid = f"{kind}:{slug}"
    subject_class = "person" if kind == "person" else "world"
    con.execute(
        "INSERT INTO identities(id, display, subject_class, pronouns, description)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET"
        "  display=excluded.display, pronouns=excluded.pronouns,"
        "  description=excluded.description",
        (eid, display, subject_class, pronouns, description),
    )
    for alias in [display.lower(), *(aliases or [])]:
        con.execute(
            "INSERT INTO entity_aliases(alias, canonical_entity_id, entity_type,"
            " confidence, source_ref) VALUES (?, ?, 'identity', 0.9, 'register_entity')"
            " ON CONFLICT(alias, canonical_entity_id) DO NOTHING",
            (alias.lower(), eid),
        )
    return eid


def register_person(
    con: sqlite3.Connection,
    name: str,
    *,
    pronouns: str | None = None,
    description: str | None = None,
    aliases: list[str] | None = None,
) -> str:
    'Register a person:<slug> identity + its aliases (names/roles only).'
    return register_entity(
        con, "person", name, pronouns=pronouns, description=description, aliases=aliases
    )
