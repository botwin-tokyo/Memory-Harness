'When a preference is restated: corroboration, variation, or correction.'

from __future__ import annotations

import json
import re

CONFIRM_OVERLAP = 0.7

MAX_FACETS = 8

_WORD = re.compile(r"[a-z0-9']+")
_STOP = frozenset("""
a an the and or but of to in on at for with without from by as is are was were
be been being his her its their my your our he she it they them him i me you we
that this those these there here then than so very just really quite always
never often sometimes usually he's she's it's dont don't
""".split())


def tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP}


def overlap(a: str, b: str) -> float:
    'Overlap coefficient: |A∩B| / min(|A|,|B|).'
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def classify(statement: str, head_statement: str,
             facets: list[str]) -> tuple[str, float, str]:
    '`("confirm"|"facet", score, matched)` for a restated preference.'
    best, matched = 0.0, ""
    for candidate in [head_statement, *facets]:
        score = overlap(statement, candidate)
        if score > best:
            best, matched = score, candidate
    if best >= CONFIRM_OVERLAP:
        return "confirm", best, matched
    if len(facets) >= MAX_FACETS:
        return "confirm", best, matched
    return "facet", best, matched


def load_facets(raw: object) -> list[str]:
    try:
        out = json.loads(str(raw or "[]"))
    except (ValueError, TypeError):
        return []
    return [str(x) for x in out] if isinstance(out, list) else []


def render(statement: str, facets: list[str]) -> str:
    'How a preference reads when it holds more than one true phrasing.'
    if not facets:
        return statement
    return statement.rstrip(". ") + ". Also: " + "; ".join(
        f.rstrip(". ") for f in facets) + "."
