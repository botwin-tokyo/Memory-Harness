"Nightly: read the day's journal, propose facts, let a person decide."

from __future__ import annotations

import json
import logging
import sqlite3

from memory_harness.curator import claimdedup

log = logging.getLogger("memory_harness.curator.journalfacts")


def _reader() -> sqlite3.Cursor:
    'A read-only cursor on the graph, for the decided-claims pool.'
    from memory_harness.config import get_settings
    from memory_harness.db.connection import open_reader

    return open_reader(get_settings().db.path).cursor()


MAX_PER_PASS = 8

MAX_ENTRIES = 3

MAX_PER_ENTRY = 5

_PROMPT = """Extract up to five distinct durable facts directly supported by this journal. Return a JSON array with fact and quote fields. Quote the supporting sentence verbatim. Entry: {entry}"""


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
    start = int(extra.get("facts_cursor") or 0) % len(nodes)
    window = (nodes + nodes)[start:start + limit]
    extra["facts_cursor"] = (start + limit) % len(nodes)
    return window


def _full_entry(c: object, lineage: str, preview: str) -> str:
    'The whole day, not just its headline.'
    try:
        r = c.harness.post(  # type: ignore[attr-defined]
            "/v1/explore",
            json={"start_id": lineage, "depth": 1, "direction": "in"},
            headers=c.headers,  # type: ignore[attr-defined]
        )
        if r.status_code != 200:
            return preview
        items = r.json().get("items") or []
        texts = [
            str(i.get("text") or "").strip()
            for i in items
            if int(i.get("hop") or 1) >= 1
            and not str(i.get("text") or "").lstrip().startswith("[journal]")
        ]
        body = "\n\n".join(t for t in texts if t)
        return body or preview
    except Exception as exc:  # noqa: BLE001 - fall back to the preview
        log.warning("could not read journal sections for %s: %s", lineage[:13], exc)
        return preview


def _claims(c: object, text: str) -> list[dict[str, str]]:
    'Ask the model service → [{fact, quote}]. A bad answer costs the entry only.'
    try:
        raw = c.llm.chat(  # type: ignore[attr-defined]
            [{"role": "user", "content": _PROMPT.format(entry=text[:6000])}]
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("journal fact extraction unavailable: %s", exc)
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
    claims: list[dict[str, str]] = []
    for x in got:
        if isinstance(x, dict):
            fact = str(x.get("fact") or "").strip()
            quote = str(x.get("quote") or "").strip()
        else:
            fact, quote = str(x).strip(), ""
        if fact and quote:
            claims.append({"fact": fact, "quote": quote})
        elif fact:
            log.info("dropping uncited claim: %s", fact[:80])
    return claims[:MAX_PER_ENTRY]


def propose_from_journals(c: object, *, max_per_pass: int = MAX_PER_PASS,
                          max_entries: int = MAX_ENTRIES) -> dict[str, object]:
    'One pass. Returns what it proposed and what it skipped.'
    proposed: list[str] = []
    skipped = 0

    for node in _entries(c, max_entries):
        if len(proposed) >= max_per_pass:
            break
        lineage = str(node.get("lineageId") or "")
        body = str(node.get("bodyPreview") or "")
        if not lineage or not body:
            continue

        pool = claimdedup.decided_claims(_reader())

        for item in _claims(c, _full_entry(c, lineage, body)):
            if len(proposed) >= max_per_pass:
                break
            claim = item["fact"]
            dup, matched, sim, how = claimdedup.already_decided(c, claim, pool)
            if dup:
                log.info("skipping reworded claim (%s, %.3f): %r ~ %r",
                         how, sim, claim[:60], matched[:60])
                skipped += 1
                continue
            r = c.harness.post(  # type: ignore[attr-defined]
                "/v1/facts/propose",
                json={"claim": claim, "source_lineage": lineage,
                      "source_note": item["quote"]},
                headers=c.headers,  # type: ignore[attr-defined]
            )
            if r.status_code != 200:
                log.warning("proposal refused (%s): %s", r.status_code, r.text[:120])
            elif r.json().get("status") == "duplicate":
                skipped += 1
            else:
                proposed.append(claim)

    log.info("journalfacts: %d proposed, %d already known", len(proposed), skipped)
    return {"proposed": proposed, "skipped": skipped}
