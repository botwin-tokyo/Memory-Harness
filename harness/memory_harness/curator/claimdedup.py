from __future__ import annotations

import logging
import sqlite3

log = logging.getLogger("memory_harness.curator.claimdedup")

CERTAIN = 0.98

ASK = 0.85

NEIGHBOURS = 12

_JUDGE = """Two statements from a personal memory system. A human has already
decided about A — approved it or rejected it.

Would showing them B be showing them the same decision twice?

SAME — B restates A. Different words, different tense, more or less detail
about the SAME event or habit. "Defers the oil change to Monday morning" and
"defers the oil change from Sunday to Monday" are one claim told twice.

DIFFERENT — B asserts something A does not. A different occasion, a different
object, a different person, a different value. "Bought groceries on Friday" and
"bought groceries on Saturday" are two claims, however alike they read.

Ask: if the human approved A, is B now already true? If yes, same.

A: {a}
B: {b}

Answer with one word: same or different."""


def decided_claims(cur: sqlite3.Cursor, *, fact_type: str = "journal") -> list[str]:
    return [
        str(r["gloss"])
        for r in cur.execute(
            "SELECT gloss FROM facts WHERE fact_type=? AND status IN"
            " ('active','retracted','superseded','disputed')", (fact_type,))
    ]


def _nearest(c: object, claim: str, pool: list[str]) -> list[tuple[str, float]]:
    'The most similar decided claims, best first. Empty if the node is down.'
    if not pool:
        return []
    try:
        sims = c.embeddings.similarity([claim], pool)[0]  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        log.warning("claim dedup degraded — embeddings unavailable: %s", exc)
        return []
    ranked = sorted(zip(pool, sims), key=lambda t: -t[1])
    return ranked[:NEIGHBOURS]


def _judge_same(c: object, a: str, b: str) -> bool | None:
    'Ask the model service. None means it could not answer.'
    try:
        answer = c.llm.chat(  # type: ignore[attr-defined]
            [{"role": "user", "content": _JUDGE.format(a=a, b=b)}]
        ).strip().lower()
    except Exception as exc:  # noqa: BLE001
        log.warning("claim dedup degraded — model service unavailable: %s", exc)
        return None
    if "same" in answer:
        return True
    if "different" in answer:
        return False
    return None


def already_decided(
    c: object, claim: str, pool: list[str]
) -> tuple[bool, str, float, str]:
    'Has a human already ruled on this claim, in any wording?'
    ranked = _nearest(c, claim, pool)
    if not ranked:
        return False, "", 0.0, "degraded"

    best, best_sim = ranked[0]
    if best_sim >= CERTAIN:
        return True, best, float(best_sim), "certain"

    for other, sim in ranked:
        if sim < ASK:
            break
        verdict = _judge_same(c, claim, other)
        if verdict is None:
            return False, "", float(sim), "degraded"
        if verdict:
            return True, other, float(sim), "judged"
    return False, "", float(best_sim), "new"
