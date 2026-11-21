from __future__ import annotations

import json
import logging
import sqlite3

log = logging.getLogger("memory_harness.curator.prefaudit")

BATCH = 12

_PROMPT = """Classify each numbered statement. Return a JSON array with n, verdict (preference, fact, or observation), and why (up to twelve words). Statements: {items}"""


def _batches(rows: list[sqlite3.Row], size: int) -> list[list[sqlite3.Row]]:
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def _judge(c: object, batch: list[sqlite3.Row]) -> dict[int, tuple[str, str]]:
    'One batch → {index: (verdict, why)}. A bad answer costs the batch.'
    listing = "\n".join(
        f"{i}. {str(r['statement'])[:300]}" for i, r in enumerate(batch))
    try:
        raw = c.llm.chat(  # type: ignore[attr-defined]
            [{"role": "user", "content": _PROMPT.format(items=listing)}]
        ).strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("preference audit unavailable: %s", exc)
        return {}
    if raw.startswith("```"):
        raw = raw.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        got = json.loads(raw)
    except ValueError:
        log.warning("audit did not return JSON: %s", raw[:120])
        return {}
    out: dict[int, tuple[str, str]] = {}
    if not isinstance(got, list):
        return out
    for x in got:
        if not isinstance(x, dict):
            continue
        try:
            n = int(str(x.get("n")))
        except (TypeError, ValueError):
            continue
        verdict = str(x.get("verdict") or "").strip().lower()
        if verdict in ("preference", "fact", "observation"):
            out[n] = (verdict, str(x.get("why") or "").strip()[:80])
    return out


def audit(c: object, con: sqlite3.Connection, *, limit: int = 0) -> dict[str, object]:
    'Every active preference, judged. Read-only.'
    rows = con.execute(
        "SELECT preference_id, lineage_id, category, item, statement"
        " FROM preferences WHERE is_head=1 AND active=1"
        " ORDER BY category, item"
    ).fetchall()
    if limit:
        rows = rows[:limit]

    counts = {"preference": 0, "fact": 0, "observation": 0, "unjudged": 0}
    flagged: list[dict[str, str]] = []
    for batch in _batches(list(rows), BATCH):
        verdicts = _judge(c, batch)
        for i, row in enumerate(batch):
            got = verdicts.get(i)
            if got is None:
                counts["unjudged"] += 1
                continue
            verdict, why = got
            counts[verdict] += 1
            if verdict != "preference":
                flagged.append({
                    "lineage_id": str(row["lineage_id"]),
                    "category": str(row["category"] or ""),
                    "item": str(row["item"] or ""),
                    "verdict": verdict,
                    "why": why,
                    "statement": str(row["statement"])[:200],
                })

    total = len(rows)
    return {
        "total": total,
        "counts": counts,
        "mistyped": len(flagged),
        "mistyped_pct": round(100 * len(flagged) / total) if total else 0,
        "flagged": flagged,
        "note": (
            "Read-only. Moving a preference to a fact means retracting one and "
            "minting the other, and the approvals queue has no card for that — "
            "adding one is a change to the operator's app."
        ),
    }
