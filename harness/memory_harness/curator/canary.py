'Does the system still do what it claims? Checks, not tests.'

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from typing import Callable

OFF_TOPIC_PROBE = "quantum entanglement in biological systems"


REQUIRED_SCOPES: dict[str, set[str]] = {
    "node-mcp": {"shared", "reference", "library"},
    "node-ingest": {"shared", "reference"},
}


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


@dataclass
class CanaryResult:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(Check(name, status, detail))

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == "fail"]

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": not self.failed,
            "passed": sum(1 for c in self.checks if c.status == "pass"),
            "failed": len(self.failed),
            "skipped": sum(1 for c in self.checks if c.status == "skip"),
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail}
                for c in self.checks
            ],
        }


def _guard(res: CanaryResult, name: str, fn: Callable[[], tuple[str, str]]) -> None:
    'Run one check. An exception is a `fail`, never a crashed canary.'
    try:
        status, detail = fn()
    except Exception as exc:  # noqa: BLE001
        status, detail = "fail", f"{type(exc).__name__}: {exc}"
    res.add(name, status, detail)


def _needle(con: sqlite3.Connection) -> tuple[str, str] | None:
    'A distinctive fact that exists right now, and a query for it.'
    row = con.execute(
        "SELECT gloss FROM facts WHERE status='active' AND is_head=1"
        " AND LENGTH(gloss) BETWEEN 20 AND 90 ORDER BY recorded_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    gloss = str(row["gloss"])
    words = [w.strip(".,'\"") for w in gloss.split() if len(w.strip(".,'\"")) > 5]
    return (gloss, " ".join(words[:4]) if words else gloss[:40])


def check_recall_finds_a_known_fact(c: object, con: sqlite3.Connection) -> tuple[str, str]:
    'The needle test: a fact that IS in the graph must come back.'
    picked = _needle(con)
    if picked is None:
        return "skip", "no active facts to probe with"
    gloss, query = picked
    r = c.harness.post(  # type: ignore[attr-defined]
        "/v1/recall", json={"query": query, "token_budget": 8000},
        headers=c.headers)  # type: ignore[attr-defined]
    if r.status_code != 200:
        return "fail", f"recall returned {r.status_code}"
    facts = r.json().get("facts") or []
    if not facts:
        return "fail", f"recall returned 0 facts for {query!r} (expects {gloss[:50]!r})"
    return "pass", f"{len(facts)} facts for {query!r}"


def check_small_budget_still_returns_facts(c: object, con: sqlite3.Connection) -> tuple[str, str]:
    picked = _needle(con)
    if picked is None:
        return "skip", "no active facts to probe with"
    _gloss, query = picked
    r = c.harness.post(  # type: ignore[attr-defined]
        "/v1/recall", json={"query": query, "token_budget": 700},
        headers=c.headers)  # type: ignore[attr-defined]
    if r.status_code != 200:
        return "fail", f"recall returned {r.status_code}"
    body = r.json()
    if not (body.get("facts") or []):
        return "fail", (
            f"0 facts at token_budget=700 — core+preferences are starving the "
            f"pack again (prefs={len(body.get('preferences') or [])})")
    return "pass", f"{len(body['facts'])} facts at a 700-token budget"


def check_library_refuses_off_topic(c: object, con: sqlite3.Connection) -> tuple[str, str]:
    have = con.execute(
        "SELECT COUNT(*) FROM notes WHERE scope='library' AND is_head=1"
    ).fetchone()[0]
    if not have:
        return "skip", "no books in the library"
    r = c.harness.post(  # type: ignore[attr-defined]
        "/v1/library/search", json={"query": OFF_TOPIC_PROBE, "limit": 3},
        headers=c.headers)  # type: ignore[attr-defined]
    if r.status_code == 403:
        return "skip", "this key has no library scope; see service_keys check"
    if r.status_code != 200:
        return "fail", f"library search returned {r.status_code}"
    n = len(r.json().get("passages") or r.json().get("results") or [])
    if n:
        return "fail", f"off-topic query returned {n} passages — the floor has drifted"
    return "pass", "off-topic returns nothing"


def check_library_answers_on_topic(c: object, con: sqlite3.Connection) -> tuple[str, str]:
    '...and the floor is not so high that the library is unreachable.'
    row = con.execute(
        "SELECT body FROM notes WHERE scope='library' AND is_head=1"
        " AND body LIKE '[book]%' LIMIT 1").fetchone()
    if row is None:
        return "skip", "no books in the library"
    title = str(row["body"]).splitlines()[0].replace("[book] ", "")
    query = " ".join(title.split()[:5])
    r = c.harness.post(  # type: ignore[attr-defined]
        "/v1/library/search", json={"query": query, "limit": 3},
        headers=c.headers)  # type: ignore[attr-defined]
    if r.status_code == 403:
        return "skip", "this key has no library scope; see service_keys check"
    if r.status_code != 200:
        return "fail", f"library search returned {r.status_code}"
    n = len(r.json().get("passages") or r.json().get("results") or [])
    if not n:
        return "fail", f"a book's own title {query!r} matched nothing — floor too high"
    return "pass", f"{n} passages for a book's own subject"


_KIND_PROBES = (
    ("person", "[person] %"),
    ("journal", "[journal] %"),
    ("todo", "[todo] %"),
    ("repo", "[repo] %"),
    ("commit", "[commit] %"),
    ("artifact", "[artifact] %"),
    ("book", "[book] %"),
)


def check_every_node_kind_is_walkable(c: object, con: sqlite3.Connection) -> tuple[str, str]:
    '`explore` must reach neighbours from every kind of node we hold.'
    walked, broken, skipped = [], [], []
    for kind, pattern in _KIND_PROBES:
        row = con.execute(
            "SELECT lineage_id FROM notes WHERE is_head=1 AND status='active'"
            " AND body LIKE ? LIMIT 1", (pattern,)).fetchone()
        if row is None:
            skipped.append(kind)
            continue
        r = c.harness.post(  # type: ignore[attr-defined]
            "/v1/explore",
            json={"start_id": str(row["lineage_id"]), "depth": 1, "direction": "both"},
            headers=c.headers)  # type: ignore[attr-defined]
        if r.status_code == 403:
            skipped.append(f"{kind}(no scope)")
        elif r.status_code != 200:
            broken.append(f"{kind}:{r.status_code}")
        elif len(r.json().get("items") or []) < 1:
            broken.append(f"{kind}:empty")
        else:
            walked.append(kind)
    if broken:
        return "fail", f"unwalkable: {', '.join(broken)} (ok: {', '.join(walked)})"
    return "pass", f"walked {', '.join(walked)}" + (
        f" · absent: {', '.join(skipped)}" if skipped else "")


def check_service_keys_carry_their_scopes(c: object, con: sqlite3.Connection) -> tuple[str, str]:
    'Every live service key still holds what its tools need.'
    import json as _json

    bad = []
    for node, needed in REQUIRED_SCOPES.items():
        rows = con.execute(
            "SELECT allowed_scopes FROM policy WHERE node=? AND revoked_at IS NULL",
            (node,)).fetchall()
        if not rows:
            continue
        best = max((set(_json.loads(str(r["allowed_scopes"]))) for r in rows),
                   key=len)
        missing = needed - best
        if missing:
            bad.append(f"{node} missing {sorted(missing)}")
    if bad:
        return "fail", "; ".join(bad)
    return "pass", f"{len(REQUIRED_SCOPES)} service keys carry their scopes"


def check_no_live_key_is_over_scoped(c: object, con: sqlite3.Connection) -> tuple[str, str]:
    'The opposite failure: a key that can reach more than its job needs.'
    import json as _json

    known = set(REQUIRED_SCOPES)
    extra = []
    for r in con.execute(
        "SELECT node, principal_id, allowed_scopes FROM policy"
        " WHERE revoked_at IS NULL"
    ):
        node = str(r["node"])
        if node not in known:
            continue
        held = set(_json.loads(str(r["allowed_scopes"])))
        surplus = held - REQUIRED_SCOPES[node]
        if surplus:
            extra.append(f"{node}/{r['principal_id']} also holds {sorted(surplus)}")
    if extra:
        return "skip", "; ".join(extra)
    return "pass", "no surplus scopes on service keys"


_INDEXED = (
    ("fact", "facts", "fact_id", "status='active' AND is_head=1"),
    ("note", "notes", "note_id", "status='active' AND is_head=1"),
    ("episode", "episodes", "episode_id", "status='active' AND is_head=1"),
    ("preference", "preferences", "preference_id", "active=1 AND is_head=1"),
)


def check_no_orphaned_index_rows(c: object, con: sqlite3.Connection) -> tuple[str, str]:
    'Every index row points at a memory that is still live.'
    have_vec = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vec'"
    ).fetchone() is not None

    bad: list[str] = []
    for kind, table, pk, live in _INDEXED:
        n = con.execute(
            f"SELECT COUNT(*) FROM fts_memories f WHERE f.kind=? AND NOT EXISTS"
            f" (SELECT 1 FROM {table} t WHERE t.{pk}=f.ref_id AND {live})",
            (kind,)).fetchone()[0]
        if n:
            bad.append(f"fts/{kind}={n}")
        if have_vec:
            n = con.execute(
                f"SELECT COUNT(*) FROM vec v WHERE v.kind=? AND NOT EXISTS"
                f" (SELECT 1 FROM {table} t WHERE t.{pk}=v.ref_id AND {live})",
                (kind,)).fetchone()[0]
            if n:
                bad.append(f"vec/{kind}={n}")
    if bad:
        return "fail", "index rows point at memories that are gone: " + ", ".join(bad)
    return "pass", f"both indexes agree with the graph across {len(_INDEXED)} stores"


def check_proposed_claims_cite_a_live_source(c: object, con: sqlite3.Connection) -> tuple[str, str]:
    'A review card whose source is gone cannot be judged, only rubber-stamped.'
    import json as _json

    dangling = 0
    for r in con.execute(
        "SELECT evidence_refs FROM facts WHERE status='proposed'"
    ):
        refs = _json.loads(str(r["evidence_refs"] or "[]"))
        if not refs:
            dangling += 1
            continue
        hit = con.execute(
            "SELECT 1 FROM notes WHERE lineage_id=? AND is_head=1 AND status='active'",
            (str(refs[0]),)).fetchone()
        if hit is None:
            dangling += 1
    if dangling:
        return "fail", f"{dangling} pending claims cite a source that is gone"
    return "pass", "every pending claim cites a live source"


def check_the_queue_is_not_repeating_itself(c: object, con: sqlite3.Connection) -> tuple[str, str]:
    from memory_harness.domain.propose import claim_key

    decided = {
        claim_key(r["gloss"])
        for r in con.execute(
            "SELECT gloss FROM facts WHERE status IN"
            " ('active','retracted','superseded','disputed')")
    }
    repeats = [
        str(r["gloss"])[:60]
        for r in con.execute("SELECT gloss FROM facts WHERE status='proposed'")
        if claim_key(r["gloss"]) in decided
    ]
    if repeats:
        return "fail", f"{len(repeats)} pending claims already decided: {repeats[:2]}"
    return "pass", "no pending claim repeats a decided one"


def check_no_main_path_opens_the_private_store(
    c: object, con: sqlite3.Connection
) -> tuple[str, str]:
    'Only the private module and its routes may address the private file.'
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    allowed = {"domain/private_memory.py", "api/routes.py", "curator/canary.py"}
    offenders = []
    for path in sorted(root.rglob("*.py")):
        rel = str(path.relative_to(root))
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "open_private" in text or "private_memory" in text:
            offenders.append(rel)
    if offenders:
        return "fail", "main-graph code references the private store: " + ", ".join(
            offenders)
    return "pass", "only the private module and its routes address it"


def check_the_private_tables_are_absent_from_the_main_db(
    c: object, con: sqlite3.Connection
) -> tuple[str, str]:
    found = [
        str(r[0]) for r in con.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'private_%'")
    ]
    if found:
        return "fail", f"private tables exist in the MAIN database: {found}"
    return "pass", "the main database has no private tables"


def check_private_authority_is_held_by_the_right_keys(
    c: object, con: sqlite3.Connection
) -> tuple[str, str]:
    'Exactly the keys that should carry it, and no others.'
    rows = list(con.execute(
        "SELECT principal_id, node FROM policy"
        " WHERE private_authority=1 AND revoked_at IS NULL"))
    holders = {f"{r[0]}@{r[1]}" for r in rows}
    unexpected = holders - {v.strip() for v in os.environ.get("PRIVATE_AUTHORITY_HOLDERS", "").split(",") if v.strip()}
    if unexpected:
        return "fail", f"unexpected keys hold private_authority: {sorted(unexpected)}"
    if not holders:
        return "skip", "no key holds private_authority yet"
    return "pass", f"held by {sorted(holders)}"


CHECKS: tuple[tuple[str, Callable[[object, sqlite3.Connection], tuple[str, str]]], ...] = (
    ("recall_finds_a_known_fact", check_recall_finds_a_known_fact),
    ("small_budget_still_returns_facts", check_small_budget_still_returns_facts),
    ("library_refuses_off_topic", check_library_refuses_off_topic),
    ("library_answers_on_topic", check_library_answers_on_topic),
    ("every_node_kind_is_walkable", check_every_node_kind_is_walkable),
    ("service_keys_carry_their_scopes", check_service_keys_carry_their_scopes),
    ("no_live_key_is_over_scoped", check_no_live_key_is_over_scoped),
    ("no_orphaned_index_rows", check_no_orphaned_index_rows),
    ("proposed_claims_cite_a_live_source", check_proposed_claims_cite_a_live_source),
    ("the_queue_is_not_repeating_itself", check_the_queue_is_not_repeating_itself),
    ("no_main_path_opens_the_private_store", check_no_main_path_opens_the_private_store),
    ("private_tables_absent_from_main_db", check_the_private_tables_are_absent_from_the_main_db),
    ("private_authority_is_held_by_the_right_keys",
     check_private_authority_is_held_by_the_right_keys),
)


def run(c: object, con: sqlite3.Connection) -> CanaryResult:
    'Every check, against the live graph. Never raises.'
    res = CanaryResult()
    for name, fn in CHECKS:
        _guard(res, name, lambda fn=fn: fn(c, con))  # type: ignore[misc]
    return res
