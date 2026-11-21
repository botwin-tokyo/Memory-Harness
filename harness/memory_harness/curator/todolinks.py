"Nightly: pair each todo day hub with that day's journal hub."

from __future__ import annotations

import logging

log = logging.getLogger("memory_harness.curator.todolinks")

MAX_PER_PASS = 30


def _hubs(c: object, kind: str) -> list[dict]:
    'Hub nodes of a kind, via the graph read-model.'
    r = c.harness.get(  # type: ignore[attr-defined]
        "/v1/graph", params={"fields": "full", "kinds": kind},
        headers=c.headers,  # type: ignore[attr-defined]
    )
    if r.status_code != 200:
        return []
    return r.json().get("nodes") or []


def _date_of(node: dict) -> str | None:
    for tag in node.get("tags") or []:
        if str(tag).startswith("date:"):
            return str(tag)[5:]
    return None


def _has_same_day(c: object, lineage: str) -> bool:
    r = c.harness.post(  # type: ignore[attr-defined]
        "/v1/explore", json={"start_id": lineage, "depth": 1,
                             "direction": "out"},
        headers=c.headers,  # type: ignore[attr-defined]
    )
    if r.status_code != 200:
        return True
    for item in r.json().get("items") or []:
        if str(item.get("lineage_id")) != lineage:
            continue
        for link in item.get("links_out") or []:
            if str(link.get("link_type")) == "same_day":
                return True
    return False


def pair_todo_days(c: object, *, max_per_pass: int = MAX_PER_PASS) -> dict[str, object]:
    'One pass. Returns what it paired and what it skipped.'
    todos = [n for n in _hubs(c, "todo")
             if "todo" in (n.get("tags") or [])
             and "todo-item" not in (n.get("tags") or [])]
    journals = {_date_of(n): str(n.get("lineageId") or "")
                for n in _hubs(c, "journal")}

    paired: list[str] = []
    skipped = 0
    for node in todos:
        if len(paired) >= max_per_pass:
            break
        hub = str(node.get("lineageId") or "")
        date = _date_of(node)
        if not hub or not date:
            continue
        journal = journals.get(date)
        if not journal:
            skipped += 1
            continue
        if _has_same_day(c, hub):
            skipped += 1
            continue
        r = c.harness.post(  # type: ignore[attr-defined]
            "/v1/link", json={"src_lineage_id": hub,
                              "dst_lineage_id": journal,
                              "link_type": "same_day"},
            headers=c.headers,  # type: ignore[attr-defined]
        )
        if r.status_code == 200:
            paired.append(date)
        else:
            log.warning("same_day %s refused (%s): %s", date, r.status_code,
                        r.text[:120])

    log.info("todolinks: %d paired, %d skipped", len(paired), skipped)
    return {"paired": paired, "skipped": skipped}
