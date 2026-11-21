from __future__ import annotations

import json
import logging

from memory_contract import person_slug

log = logging.getLogger("memory_harness.curator.peoplepass")

MAX_PER_PASS = 6

MAX_ENTRIES = 3

MAX_MERGES = 2

_NOT_PEOPLE = {
    "companion", "self", "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "january", "february", "march", "april", "may",
    "june", "july", "august", "september", "october", "november", "december",

}

_PROMPT = """List only named people from the journal. For each return name, quote (verbatim supporting sentence), and relationship (empty if unknown). Return only a JSON array. Entry: {entry}"""


def _entries(c: object, limit: int) -> list[dict]:
    'A rotating window of journal hubs, newest first.'
    r = c.harness.get(  # type: ignore[attr-defined]
        "/v1/graph", params={"fields": "full", "kinds": "journal"},
        headers=c.headers,  # type: ignore[attr-defined]
    )
    if r.status_code != 200:
        return []
    nodes = r.json().get("nodes") or []
    if not nodes:
        return []
    nodes.sort(key=lambda n: str(n.get("updatedAt") or ""), reverse=True)

    extra = getattr(c, "extra", None)
    if extra is None:
        return nodes[:limit]
    start = int(extra.get("people_cursor") or 0) % len(nodes)
    window = (nodes + nodes)[start:start + limit]
    extra["people_cursor"] = (start + limit) % len(nodes)
    return window


def _named(c: object, text: str) -> list[dict[str, str]]:
    'Ask the model service → [{name, quote, relationship}].'
    try:
        raw = c.llm.chat(  # type: ignore[attr-defined]
            [{"role": "user", "content": _PROMPT.format(entry=text[:6000])}]
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("person extraction unavailable: %s", exc)
        return []
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        got = json.loads(raw)
    except ValueError:
        log.warning("extraction did not return JSON: %s", raw[:120])
        return []
    if not isinstance(got, list):
        return []

    out: list[dict[str, str]] = []
    for x in got:
        if not isinstance(x, dict):
            continue
        name = str(x.get("name") or "").strip()
        quote = str(x.get("quote") or "").strip()
        if not name or not quote:
            if name:
                log.info("dropping uncited name: %s", name[:60])
            continue
        if name.lower() in _NOT_PEOPLE:
            continue
        if name.lower() not in quote.lower():
            log.info("dropping name absent from its own quote: %s", name[:60])
            continue
        out.append({"name": name, "quote": quote,
                    "relationship": str(x.get("relationship") or "").strip()[:60]})
    return out


def _people(c: object) -> dict[str, str]:
    'Existing person hubs → {slug: display}, from the graph read-model.'
    r = c.harness.get(  # type: ignore[attr-defined]
        "/v1/graph", params={"fields": "full", "kinds": "person"},
        headers=c.headers,  # type: ignore[attr-defined]
    )
    if r.status_code != 200:
        return {}
    found: dict[str, str] = {}
    for n in r.json().get("nodes") or []:
        head = str(n.get("bodyPreview") or "").splitlines()[:1]
        if not head or not head[0].startswith("[person] "):
            continue
        display = head[0][len("[person] "):].strip()
        if display and display.lower() not in ("companion", "self"):
            found[person_slug(display)] = display
    return found


def merge_candidates(people: dict[str, str]) -> list[tuple[str, str]]:
    'Pairs `(duplicate, survivor)` that look like one person.'
    out: list[tuple[str, str]] = []
    for short, short_display in people.items():
        for long, _ in people.items():
            if short == long:
                continue
            if long.startswith(short + "-"):
                out.append((short, long))
                log.info("merge candidate: %s → %s", short_display, long)
    return out


def propose_people(c: object, *, max_per_pass: int = MAX_PER_PASS,
                   max_entries: int = MAX_ENTRIES,
                   max_merges: int = MAX_MERGES) -> dict[str, object]:
    'One pass. Returns what it proposed. Never creates a person.'
    from memory_harness.curator.journalfacts import _full_entry

    proposed: list[str] = []
    known_already = 0

    for node in _entries(c, max_entries):
        if len(proposed) >= max_per_pass:
            break
        lineage = str(node.get("lineageId") or "")
        body = str(node.get("bodyPreview") or "")
        if not lineage or not body:
            continue

        for item in _named(c, _full_entry(c, lineage, body)):
            if len(proposed) >= max_per_pass:
                break
            r = c.harness.post(  # type: ignore[attr-defined]
                "/v1/people/propose",
                json={"name": item["name"], "sentence": item["quote"],
                      "relationship": item["relationship"]},
                headers=c.headers,  # type: ignore[attr-defined]
            )
            if r.status_code != 200:
                log.warning("person proposal refused (%s): %s",
                            r.status_code, r.text[:120])
                continue
            if r.json().get("status") == "proposed":
                proposed.append(item["name"])
            else:
                known_already += 1

    merges: list[str] = []
    people = _people(c)
    for short, long in merge_candidates(people)[:max_merges]:
        r = c.harness.post(  # type: ignore[attr-defined]
            "/v1/people/propose-merge",
            json={"slug": short, "merge_into": long,
                  "sentence": f"{people[long]} and {people[short]} appear to be "
                              f"the same person — one name is the other's."},
            headers=c.headers,  # type: ignore[attr-defined]
        )
        if r.status_code == 200 and r.json().get("status") == "proposed":
            merges.append(f"{short}→{long}")
        elif r.status_code not in (200, 409):
            log.warning("merge proposal refused (%s): %s", r.status_code, r.text[:120])

    log.info("peoplepass: %d proposed, %d already known, %d merges",
             len(proposed), known_already, len(merges))
    return {"proposed": proposed, "known": known_already, "merges": merges}
