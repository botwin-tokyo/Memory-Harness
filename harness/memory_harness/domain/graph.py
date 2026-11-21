'Active-link expansion + explore (POST /v1/explore). Steps 4.2/4.3.'

from __future__ import annotations

import json
import sqlite3
from typing import cast

from memory_harness.domain.links import find_lineage_head, require_visible
from memory_contract import COLLAPSED_TAGS, node_kind

from memory_harness.domain.scope import visible_scopes
from memory_harness.models.api import LinkRef
from memory_harness.models.domain import Principal

_TEXT_COL = {
    "facts": "gloss",
    "preferences": "statement",
    "episodes": "summary",
    "notes": "body",
    "procedures": "steps_md",
}


def head_gloss(cur: sqlite3.Cursor | sqlite3.Connection, lineage_id: str) -> str:
    found = find_lineage_head(cur, lineage_id)
    if found is None:
        return ""
    table, row = found
    return str(row[_TEXT_COL[table]])


def active_links_from(
    cur: sqlite3.Cursor | sqlite3.Connection, lineage_id: str, scopes: set[str]
) -> list[sqlite3.Row]:
    '`active` edges OUT of a lineage, in visible scopes. Never `proposed`.'
    if not scopes:
        return []
    ph = ",".join("?" for _ in scopes)
    return cur.execute(
        f"SELECT * FROM memory_links WHERE src_lineage_id=? AND status='active'"
        f" AND scope IN ({ph})",
        (lineage_id, *sorted(scopes)),
    ).fetchall()


def active_links_to(
    cur: sqlite3.Cursor | sqlite3.Connection, lineage_id: str, scopes: set[str]
) -> list[sqlite3.Row]:
    if not scopes:
        return []
    ph = ",".join("?" for _ in scopes)
    return cur.execute(
        f"SELECT * FROM memory_links WHERE dst_lineage_id=? AND status='active'"
        f" AND scope IN ({ph})",
        (lineage_id, *sorted(scopes)),
    ).fetchall()


LINK_GLOSS_CHARS = 90


def links_out_map(cur: sqlite3.Cursor | sqlite3.Connection, lineage_id: str, scopes: set[str]) -> list[LinkRef]:
    out: list[LinkRef] = []
    for e in active_links_from(cur, lineage_id, scopes):
        out.append(
            LinkRef(
                link_type=str(e["link_type"]),
                target_lineage_id=str(e["dst_lineage_id"]),
                gloss=head_gloss(cur, str(e["dst_lineage_id"]))[:LINK_GLOSS_CHARS],
                proposed=False,
            )
        )
    if scopes:
        ph = ",".join("?" for _ in scopes)
        for e in cur.execute(
            f"SELECT * FROM memory_links WHERE src_lineage_id=? AND status='proposed'"
            f" AND scope IN ({ph})",
            (lineage_id, *sorted(scopes)),
        ).fetchall():
            out.append(
                LinkRef(
                    link_type=str(e["link_type"]),
                    target_lineage_id=str(e["dst_lineage_id"]),
                    gloss=head_gloss(cur, str(e["dst_lineage_id"]))[:LINK_GLOSS_CHARS],
                    proposed=True,
                )
            )
    return out


def expand(
    cur: sqlite3.Cursor | sqlite3.Connection,
    hits: list[tuple[str, float]],
    scopes: set[str],
    *,
    hops: int,
    decay: float,
    budget: int,
) -> list[tuple[str, float, str, sqlite3.Row]]:
    out: dict[str, float] = {}
    frontier: list[tuple[str, int, float]] = [(lid, 0, s) for lid, s in hits]
    seen: set[str] = set()
    while frontier:
        lid, d, base = frontier.pop()
        if lid in seen or d >= hops:
            continue
        seen.add(lid)
        for e in active_links_from(cur, lid, scopes):
            if len(out) >= budget:
                break
            dst = str(e["dst_lineage_id"])
            nscore = base * decay * float(e["weight"])
            if dst in seen or nscore <= out.get(dst, 0.0):
                continue
            out[dst] = nscore
            frontier.append((dst, d + 1, nscore))
    results: list[tuple[str, float, str, sqlite3.Row]] = []
    for lid, score in out.items():
        found = find_lineage_head(cur, lid)
        if found is None:
            continue
        if str(found[1]["scope"]) not in scopes:
            continue
        results.append((lid, score, found[0], found[1]))
    return results


def explore(
    cur: sqlite3.Cursor | sqlite3.Connection,
    p: Principal,
    start_id: str,
    depth: int,
    link_types: list[str] | None = None,
    max_depth: int = 5,
    direction: str = "out",
) -> list[tuple[str, sqlite3.Row, int]]:
    depth = min(depth, max_depth)
    scopes = visible_scopes(p)
    table, row = require_visible(p, cur, start_id)
    out: list[tuple[str, sqlite3.Row, int]] = [(table, row, 0)]
    seen = {start_id}
    frontier: list[tuple[str, int]] = [(start_id, 0)]
    while frontier:
        lid, d = frontier.pop(0)
        if d >= depth:
            continue
        edges: list[tuple[sqlite3.Row, str]] = []
        if direction in ("out", "both"):
            edges += [(e, "dst_lineage_id") for e in active_links_from(cur, lid, scopes)]
        if direction in ("in", "both"):
            edges += [(e, "src_lineage_id") for e in active_links_to(cur, lid, scopes)]
        for e, far_col in edges:
            if link_types and str(e["link_type"]) not in link_types:
                continue
            dst = str(e[far_col])
            if dst in seen:
                continue
            seen.add(dst)
            found = find_lineage_head(cur, dst)
            if found is None:
                continue
            t, r = found
            if str(r["scope"]) not in scopes:
                continue
            out.append((t, r, d + 1))
            frontier.append((dst, d + 1))
    return out


_SNAPSHOT_TABLES = (
    ("facts", "fact_id", "fact", "gloss"),
    ("preferences", "preference_id", "preference", "statement"),
    ("episodes", "episode_id", "episode", "summary"),
    ("notes", "note_id", "note", "body"),
    ("procedures", "procedure_id", "procedure", "steps_md"),
)
_PREVIEW_LEN = 240


def graph_snapshot(
    con: sqlite3.Connection,
    p: Principal,
    *,
    include_superseded: bool = False,
    include_archived: bool = False,
    include_proposed: bool = False,
    limit: int = 0,
    fields: str = "full",
    kinds: set[str] | None = None,
    ids: set[str] | None = None,
    ids_force_full: bool = True,
) -> dict[str, object]:
    'One-call Graph snapshot for API clients.'
    scopes = visible_scopes(p)
    out_nodes: list[dict[str, object]] = []
    if not scopes:
        return {"nodes": [], "links": [], "nodes_total": 0}
    scope_ph = ",".join("?" for _ in scopes)
    scope_args = tuple(sorted(scopes))

    emotions = {
        int(r["emotion_id"]): (str(r["name"]), str(r["family"]))
        for r in con.execute("SELECT emotion_id, name, family FROM emotions")
    }
    meta_by_target: dict[str, tuple[int, str]] = {}
    for r in con.execute(
        "SELECT target_id, created_on_node, recorded_at FROM events"
        " WHERE target_type IN ('fact','preference','episode','note','credential','procedure')"
        " ORDER BY recorded_at ASC"
    ):
        meta_by_target[str(r["target_id"])] = (int(r["recorded_at"]), str(r["created_on_node"]))

    def node(table: str, pk: str, tname: str, text_col: str, r: sqlite3.Row, status: str) -> dict[str, object]:
        row_id = str(r[pk])
        if table == "notes" and str(r["note_type"]) == "credential":
            tname = "credential"
        recorded, on_node = meta_by_target.get(row_id, (0, "unknown"))
        eid = int(r["emotion_id"])
        ename, efamily = emotions.get(eid, ("Neutrality", "neutral"))
        return {
            "id": row_id,
            "lineageId": str(r["lineage_id"]),
            "revision": int(r["revision"]),
            "isHead": bool(r["is_head"]),
            "type": tname,
            "emotionId": eid,
            "emotionFamily": efamily,
            "emotionName": ename,
            "scope": str(r["scope"]),
            "sourceTrust": str(r["source_trust"]) if "source_trust" in r.keys() and r["source_trust"] else "agent",
            "status": status,
            "confidence": float(r["confidence"]),
            "about": (
                [str(x) for x in json.loads(str(r["about"]))] if "about" in r.keys() else []
            ),
            "tags": (
                [str(x) for x in json.loads(str(r["intent_tags"]))]
                if table == "procedures"
                else [str(x) for x in json.loads(str(r["tags"]))]
            ),
            "bodyPreview": str(r[text_col])[:_PREVIEW_LEN],
            "createdByAgent": str(r["created_by_agent"]),
            "createdOnNode": on_node,
            "updatedAt": recorded,
            "subjectClass": str(r["subject_class"]) if "subject_class" in r.keys() else "world",
        }

    head_by_lineage: dict[str, str] = {}
    for table, pk, tname, text_col in _SNAPSHOT_TABLES:
        if table == "preferences":
            base = f"SELECT * FROM {table} WHERE scope IN ({scope_ph}) AND is_head=1 AND active=1"
            rows = list(con.execute(base, scope_args))
            if include_superseded:
                rows += list(con.execute(
                    f"SELECT * FROM {table} WHERE scope IN ({scope_ph}) AND is_head=0", scope_args))
        elif table == "procedures":
            rows = list(con.execute(
                f"SELECT * FROM {table} WHERE scope IN ({scope_ph}) AND is_head=1"
                " AND validation_status!='retracted'", scope_args))
        else:
            statuses = ["'active'", "'disputed'"]
            if include_superseded:
                statuses.append("'superseded'")
            if include_archived:
                statuses.append("'archived'")
            rows = list(con.execute(
                f"SELECT * FROM {table} WHERE scope IN ({scope_ph}) AND status IN ({','.join(statuses)})",
                scope_args,
            ))
        for r in rows:
            if table == "preferences":
                status = "active" if r["active"] else "superseded"
            elif table == "procedures":
                status = "active"
            else:
                status = str(r["status"])
            n = node(table, pk, tname, text_col, r, status)
            node_tags = n.get("tags")
            if isinstance(node_tags, list) and set(node_tags) & set(COLLAPSED_TAGS):
                continue
            out_nodes.append(n)
            if n["isHead"] and status == "active":
                head_by_lineage[str(n["lineageId"])] = str(n["id"])

    DRAW_FIELDS = {
        "id", "lineageId", "kind", "type", "subjectClass", "emotionId",
        "sourceTrust", "confidence", "isHead", "status", "scope",
    }
    node_ids_before_filter = {str(n["id"]) for n in out_nodes}
    for n in out_nodes:
        tags = n.get("tags")
        n["kind"] = node_kind(tags if isinstance(tags, list) else [])
    if kinds:
        out_nodes = [n for n in out_nodes if n["kind"] in kinds]
    if ids:
        out_nodes = [n for n in out_nodes
                     if str(n["id"]) in ids or str(n["lineageId"]) in ids]
        if ids_force_full:
            fields = "full"
    if fields == "minimal":
        out_nodes = [{k: v for k, v in n.items() if k in DRAW_FIELDS}
                     for n in out_nodes]

    nodes_total = len(out_nodes)
    if limit:
        out_nodes = out_nodes[:limit]

    link_statuses = "'active','proposed'" if include_proposed else "'active'"
    out_links: list[dict[str, object]] = []
    for l in con.execute(
        f"SELECT * FROM memory_links WHERE status IN ({link_statuses}) AND scope IN ({scope_ph})",
        scope_args,
    ):
        src = head_by_lineage.get(str(l["src_lineage_id"]))
        dst = head_by_lineage.get(str(l["dst_lineage_id"]))
        if src is None or dst is None:
            continue
        out_links.append({
            "id": str(l["link_id"]),
            "source": src,
            "target": dst,
            "linkType": str(l["link_type"]),
            "status": str(l["status"]),
            "weight": float(l["weight"]),
        })
    kept = {str(n["id"]) for n in out_nodes}
    if len(kept) != len(node_ids_before_filter):
        out_links = [l for l in out_links
                     if str(l["source"]) in kept and str(l["target"]) in kept]

    return {"nodes": out_nodes, "links": out_links, "nodes_total": nodes_total}


def _chunked(items: list[str], size: int = 500):  # type: ignore[no-untyped-def]
    for i in range(0, len(items), size):
        yield items[i : i + size]


def graph_delta(
    con: sqlite3.Connection,
    p: Principal,
    *,
    since: int,
    include_proposed: bool = False,
) -> dict[str, object]:
    scopes = visible_scopes(p)
    head_seq = int(con.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0])
    empty: dict[str, object] = {
        "nodes": [], "links": [], "removed": [], "cursor": head_seq,
        "full_resync": False,
    }
    if not scopes:
        return empty

    snap = graph_snapshot(con, p, fields="minimal", include_proposed=include_proposed,
                          ids_force_full=False)
    snap_nodes = cast(list[dict[str, object]], snap["nodes"])
    snap_links = cast(list[dict[str, object]], snap["links"])

    if since <= 0 or since > head_seq:
        return {"nodes": snap_nodes, "links": snap_links, "removed": [],
                "cursor": head_seq, "full_resync": True}

    scope_ph = ",".join("?" for _ in scopes)
    touched_rows: set[str] = set()
    touched_links: set[str] = set()
    for r in con.execute(
        f"SELECT target_id, target_type FROM events WHERE seq>? AND scope IN ({scope_ph})",
        (since, *sorted(scopes)),
    ):
        tid, ttype = r["target_id"], r["target_type"]
        if not tid or not ttype:
            continue
        (touched_links if str(ttype) == "link" else touched_rows).add(str(tid))

    affected: set[str] = set()
    ids = sorted(touched_rows)
    for table, pk, _t, _c in _SNAPSHOT_TABLES:
        for chunk in _chunked(ids):
            ph = ",".join("?" for _ in chunk)
            for row in con.execute(
                f"SELECT lineage_id FROM {table} WHERE {pk} IN ({ph})", chunk
            ):
                affected.add(str(row["lineage_id"]))

    nodes = [n for n in snap_nodes if str(n["lineageId"]) in affected]
    node_ids = {str(n["id"]) for n in nodes}
    links = [l for l in snap_links
             if str(l["id"]) in touched_links
             or str(l["source"]) in node_ids or str(l["target"]) in node_ids]
    present = {str(n["lineageId"]) for n in nodes}
    return {"nodes": nodes, "links": links,
            "removed": sorted(affected - present),
            "cursor": head_seq, "full_resync": False}
