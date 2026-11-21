from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

import memory_contract as contract
from memory_harness.domain.core_memory import estimate_tokens
from memory_harness.domain.graph import active_links_from, active_links_to
from memory_harness.domain.links import find_lineage_head, require_visible
from memory_harness.domain.recall import _fts_query, _knn
from memory_harness.domain.scope import visible_scopes
from memory_harness.models.domain import Principal

ARTIFACT_TAG = contract.ARTIFACT_TAG
CHUNK_TAG = contract.CHUNK_TAG
GROUNDS_LINK = contract.GROUNDS_LINK
PART_OF = contract.PART_OF_LINK

_CHUNK_INDEX_RE = re.compile(r"^chunk:(\d+)$")
_TITLE_RE = re.compile(r"^\[(?:artifact|conversation)\]\s*(.+)$", re.MULTILINE)
_KIND_RE = re.compile(r"^kind:\s*([^\s·]+)", re.MULTILINE)
_ROUTE_RE = re.compile(r"route:\s*([^\s·]+)", re.MULTILINE)
_SOURCE_RE = re.compile(r"^source:\s*(.+)$", re.MULTILINE)
_SHA_BODY_RE = re.compile(r"^sha256:\s*([0-9a-f]*)", re.MULTILINE)
_SHA_TAG_RE = re.compile(r"^src-sha:([0-9a-f]+)$")


@dataclass
class Citation:
    title: str = ""
    ref: str = ""
    sha256: str = ""
    kind: str = ""
    route: str = ""
    artifact_lineage: str = ""


@dataclass
class Chunk:
    index: int
    text: str
    ref_id: str
    relevance: float | None = None


@dataclass
class _Group:
    'One search hit plus the neighbouring passages that give it context.'

    primary: Chunk
    chunks: list[Chunk]
    relevance: float


@dataclass
class SourceResult:
    source: Citation
    chunks: list[Chunk] = field(default_factory=list)
    grounded_memory: str | None = None
    mode: str = "navigation"
    total_chunks: int = 0
    truncated: bool = False
    token_estimate: int = 0
    note: str | None = None


def _tags(row: sqlite3.Row) -> list[str]:
    import json

    raw = row["tags"] if "tags" in row.keys() else None
    if not raw:
        return []
    try:
        return [str(t) for t in json.loads(raw)]
    except (ValueError, TypeError):
        return []


def _citation(lineage: str, row: sqlite3.Row) -> Citation:
    body = str(row["body"]) if "body" in row.keys() else ""
    tags = _tags(row)
    sha = ""
    for t in tags:
        m = _SHA_TAG_RE.match(t)
        if m:
            sha = m.group(1)
            break
    if not sha:
        m = _SHA_BODY_RE.search(body)
        sha = m.group(1) if m else ""

    def one(pattern: re.Pattern[str]) -> str:
        m = pattern.search(body)
        return m.group(1).strip() if m else ""

    return Citation(
        title=one(_TITLE_RE),
        ref=one(_SOURCE_RE),
        sha256=sha,
        kind=one(_KIND_RE),
        route=one(_ROUTE_RE),
        artifact_lineage=lineage,
    )


_SOURCE_TAGS = (ARTIFACT_TAG, contract.CONVERSATION_TAG)


def _is_artifact(row: sqlite3.Row) -> bool:
    return any(t in _tags(row) for t in _SOURCE_TAGS)


def _parent_source(
    con: sqlite3.Connection, lineage: str, scopes: set[str]
) -> tuple[str, sqlite3.Row] | None:
    "A chunk's parent source, via `part_of`."
    for e in active_links_from(con, lineage, scopes):
        if str(e["link_type"]) != contract.PART_OF_LINK:
            continue
        found = find_lineage_head(con, str(e["dst_lineage_id"]))
        if found is None:
            continue
        prow = found[1]
        if str(prow["scope"]) in scopes and _is_artifact(prow):
            return str(e["dst_lineage_id"]), prow
    return None


def resolve_artifact(
    con: sqlite3.Connection, p: Principal, target: str
) -> tuple[str | None, sqlite3.Row | None, str | None, str | None]:
    'target → (artifact_lineage, artifact_row, grounded_memory, note).'
    _table, row = require_visible(p, con, target)
    if _is_artifact(row):
        return target, row, None, None

    scopes = visible_scopes(p)
    candidates: list[tuple[int, str, sqlite3.Row]] = []
    for e in active_links_from(con, target, scopes):
        if str(e["link_type"]) != GROUNDS_LINK:
            continue
        dst = str(e["dst_lineage_id"])
        found = find_lineage_head(con, dst)
        if found is None:
            continue
        drow = found[1]
        if str(drow["scope"]) not in scopes:
            continue
        if not _is_artifact(drow):
            climbed = _parent_source(con, dst, scopes)
            if climbed is None:
                continue
            dst, drow = climbed
        recorded = int(drow["recorded_at"]) if "recorded_at" in drow.keys() else 0
        candidates.append((recorded, dst, drow))

    if not candidates:
        return None, None, target, "this memory grounds no source"
    candidates.sort(key=lambda c: c[0], reverse=True)
    _, lineage, arow = candidates[0]
    note = None
    if len(candidates) > 1:
        title = _citation(lineage, arow).title or lineage
        note = (
            f"{len(candidates)} sources grounded; returning “{title}” — pass an "
            "artifact lineage as `target` to read another"
        )
    return lineage, arow, target, note


def gather_chunks(
    con: sqlite3.Connection, p: Principal, artifact_lineage: str
) -> list[Chunk]:
    "The artifact's chunks in reading order, scope-checked."
    scopes = visible_scopes(p)
    out: list[Chunk] = []
    for e in active_links_to(con, artifact_lineage, scopes):
        if str(e["link_type"]) != PART_OF:
            continue
        src = str(e["src_lineage_id"])
        found = find_lineage_head(con, src)
        if found is None:
            continue
        table, row = found
        if table != "notes" or str(row["scope"]) not in scopes:
            continue
        tags = _tags(row)
        if CHUNK_TAG not in tags:
            continue
        index = -1
        for t in tags:
            m = _CHUNK_INDEX_RE.match(t)
            if m:
                index = int(m.group(1))
                break
        out.append(Chunk(index=index, text=str(row["body"]), ref_id=str(row["note_id"])))
    out.sort(key=lambda c: (c.index < 0, c.index, c.ref_id))
    return out


def _budget(chunks: list[Chunk], token_budget: int) -> tuple[list[Chunk], int, bool]:
    kept: list[Chunk] = []
    total = 0
    for c in chunks:
        toks = estimate_tokens(c.text)
        if kept and total + toks > token_budget:
            return kept, total, True
        kept.append(c)
        total += toks
    return kept, total, False


def _budget_groups(
    groups: list[_Group], token_budget: int
) -> tuple[list[Chunk], int, bool]:
    'Budget best-group-first, and guarantee the top hit itself survives.'
    kept: list[Chunk] = []
    total = 0
    truncated = False
    for gi, g in enumerate(groups):
        cost = sum(estimate_tokens(c.text) for c in g.chunks)
        if total + cost <= token_budget:
            kept.extend(g.chunks)
            total += cost
            continue
        if gi == 0:
            kept.append(g.primary)
            total += estimate_tokens(g.primary.text)
        truncated = True
        break
    return kept, total, truncated


def gather_source(
    con: sqlite3.Connection,
    p: Principal,
    target: str,
    *,
    query: str | None = None,
    offset: int = 0,
    limit: int | None = None,
    window: int = 1,
    token_budget: int = 1500,
    qvec: list[float] | None = None,
) -> SourceResult:
    'The whole call: entry resolution → chunks → mode → citation → budget.'
    artifact_lineage, arow, grounded, note = resolve_artifact(con, p, target)
    if artifact_lineage is None or arow is None:
        return SourceResult(
            source=Citation(), grounded_memory=grounded, note=note,
            mode="search" if query else "navigation",
        )

    citation = _citation(artifact_lineage, arow)
    all_chunks = gather_chunks(con, p, artifact_lineage)
    if not all_chunks:
        return SourceResult(
            source=citation, grounded_memory=grounded, mode="search" if query else "navigation",
            note=note or "this source has no readable chunks",
        )

    if query:
        groups = _search(con, query, all_chunks, window, qvec,
                         max_hits=limit if limit and limit > 0 else 3)
        kept, tokens, truncated = _budget_groups(groups, token_budget)
        mode = "search"
    else:
        window_slice = all_chunks[offset:]
        if limit is not None:
            window_slice = window_slice[:limit]
        kept, tokens, truncated = _budget(window_slice, token_budget)
        mode = "navigation"
    return SourceResult(
        source=citation,
        chunks=kept,
        grounded_memory=grounded,
        mode=mode,
        total_chunks=len(all_chunks),
        truncated=truncated or (mode == "navigation" and offset + len(kept) < len(all_chunks)),
        token_estimate=tokens,
        note=note,
    )


def _search(
    con: sqlite3.Connection,
    query: str,
    all_chunks: list[Chunk],
    window: int,
    qvec: list[float] | None,
    max_hits: int = 3,
) -> list[_Group]:
    "Rank THIS artifact's chunks, best first, each hit carrying context."
    by_ref = {c.ref_id: c for c in all_chunks}
    scores: dict[str, float] = {}

    match = _fts_query(query)
    if match:
        ph = ",".join("?" for _ in by_ref)
        rows = con.execute(
            f"SELECT ref_id, bm25(fts_memories) AS rank FROM fts_memories"
            f" WHERE fts_memories MATCH ? AND kind='note' AND ref_id IN ({ph})"
            " ORDER BY rank LIMIT 50",
            (match, *sorted(by_ref)),
        ).fetchall()
        if rows:
            worst = min(float(r["rank"]) for r in rows)
            for r in rows:
                rel = float(r["rank"]) / worst if worst < 0 else 1.0
                ref = str(r["ref_id"])
                scores[ref] = max(scores.get(ref, 0.0), rel)

    if qvec is not None:
        for h in _knn(con, qvec, 200):
            ref = str(h["ref_id"])
            if str(h["kind"]) != "note" or ref not in by_ref:
                continue
            rel = 1.0 - float(h["distance"])
            scores[ref] = max(scores.get(ref, 0.0), rel)

    if not scores:
        return []

    ordered = sorted(scores.items(), key=lambda kv: (kv[1], -by_ref[kv[0]].index),
                     reverse=True)
    index_of = {c.index: c for c in all_chunks}
    claimed: set[str] = set()
    groups: list[_Group] = []
    for ref, rel in ordered[:max_hits]:
        if ref in claimed:
            continue
        hit = by_ref[ref]
        claimed.add(ref)
        members = [Chunk(index=hit.index, text=hit.text, ref_id=hit.ref_id,
                         relevance=rel)]
        for i in range(hit.index - window, hit.index + window + 1):
            neighbour = index_of.get(i)
            if neighbour is None or neighbour.ref_id in claimed:
                continue
            claimed.add(neighbour.ref_id)
            members.append(Chunk(index=neighbour.index, text=neighbour.text,
                                 ref_id=neighbour.ref_id, relevance=None))
        members.sort(key=lambda c: c.index)
        groups.append(_Group(primary=members[0] if len(members) == 1 else
                             next(m for m in members if m.ref_id == hit.ref_id),
                             chunks=members, relevance=rel))
    return groups
