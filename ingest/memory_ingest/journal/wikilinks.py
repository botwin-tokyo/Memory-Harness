'`[[Wiki Links]]` in a journal → edges in the graph.'

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("memory_ingest.journal.wikilinks")

_LINK_RE = re.compile(r"\[\[([^\[\]]{1,120})\]\]")

CONTEXT_CHARS = 160

DECISIVE_MARGIN = 0.15


@dataclass(frozen=True)
class WikiLink:
    topic: str
    text: str
    section: str
    context: str


def extract(sections: dict[str, str]) -> list[WikiLink]:
    'Every `[[Topic]]` in the entry, with the prose around it.'
    seen: dict[str, WikiLink] = {}
    for section, body in sections.items():
        for m in _LINK_RE.finditer(body or ""):
            text = m.group(1).strip()
            if not text:
                continue
            topic = text.lower()
            if topic in seen:
                continue
            start = max(0, m.start() - CONTEXT_CHARS // 2)
            end = min(len(body), m.end() + CONTEXT_CHARS // 2)
            seen[topic] = WikiLink(topic=topic, text=text, section=section,
                                   context=body[start:end].strip())
    return list(seen.values())


@dataclass
class Resolution:
    link: WikiLink
    target_lineage: str | None = None
    outcome: str = "unresolved"
    candidates: int = 0


def resolve(
    links: list[WikiLink],
    search: Callable[[str], list[dict]],
    *,
    margin: float = DECISIVE_MARGIN,
) -> list[Resolution]:
    'Decide what each link points at. `search(topic) -> [{lineage_id, text}]`.'
    out: list[Resolution] = []
    for link in links:
        try:
            hits = search(link.topic) or []
        except Exception as exc:  # noqa: BLE001 - one bad lookup, not the entry
            log.warning("wikilink lookup failed for %r: %s", link.topic, exc)
            out.append(Resolution(link=link))
            continue

        res = Resolution(link=link, candidates=len(hits))
        if not hits:
            log.info("wikilink %r matches nothing", link.topic)
            out.append(res)
            continue

        best = hits[0]
        res.target_lineage = str(best["lineage_id"])
        exact = str(best.get("text", "")).lower().find(link.topic) >= 0
        decisive = len(hits) == 1 or exact or _margin_ok(hits, margin)
        res.outcome = "linked" if decisive else "proposed"
        out.append(res)
    return out


def _margin_ok(hits: list[dict], margin: float) -> bool:
    'True when the best candidate clearly beats the runner-up.'
    try:
        first = float(hits[0].get("score"))  # type: ignore[arg-type]
        second = float(hits[1].get("score"))  # type: ignore[arg-type]
    except (TypeError, ValueError, IndexError, KeyError):
        return False
    return (first - second) >= margin
