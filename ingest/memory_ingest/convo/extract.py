'Mining durable memories out of a conversation, grounded to what was said.'

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from memory_contract import (
    NEUTRALITY_EMOTION_NAME,
    PREFERENCE_DEFAULT_CATEGORY,
    slugify_item,
)

log = logging.getLogger("memory_ingest.convo.extract")

MAX_CONFIDENCE = 0.6
EXTRACT_TYPES = ("fact", "preference", "note")
_SELF_RECALL = re.compile(r"(?i)\b(recall|remember)\s*\(|\[memory\]|self-recall")

PROMPT = """You are reading one window of a real conversation and deciding what
durable knowledge it contains. Most windows contain NOTHING durable — small
talk, thinking aloud, tool noise. Returning an empty list is the common and
correct answer.

Output ONLY a JSON array. Each element:
{{"type": "fact" | "preference" | "note",
  "body": "<one clear standalone statement, third person>",
  "emotion": "<exactly one name from the catalog below>",
  "confidence": <0.0-1.0>,
  "about": ["<who or what it concerns>"],
  "tags": ["<topic>"],
  "category": "<preferences only: the group, e.g. beverage>",
  "item": "<preferences only: which one, e.g. coffee>",
  "decision": true | false}}

Rules:
- Only what the conversation ACTUALLY establishes. Never infer beyond it.
- A `preference` needs BOTH `category` and `item`.
- Mark `decision: true` when the turn settles a course of action.
- `body` must stand alone — someone reading it a year from now has no transcript.
- No secrets, keys or tokens in `body`, ever.

CATALOG (the only permitted emotion names):
{catalog}

CONVERSATION WINDOW:
{window}
"""


@dataclass
class Candidate:
    'One proposed memory, and the chunk it came from.'

    type: str
    body: str
    emotion: str
    chunk_lineage: str
    confidence: float = MAX_CONFIDENCE
    about: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    category: str | None = None
    item: str | None = None
    decision: bool = False

    def payload(self, scope: str) -> dict:
        'The `/v1/intake` form for this candidate.'
        tags = list(self.tags)
        if self.decision and "decision" not in tags:
            tags.append("decision")
        if "conversation" not in tags:
            tags.append("conversation")
        out: dict = {
            "type": self.type, "body": self.body, "emotion": self.emotion,
            "scope": scope, "about": self.about, "tags": tags,
            "confidence": self.confidence, "source_class": "external",
        }
        if self.type == "preference":
            out["category"], out["item"] = self.category, self.item
        return out


def _clean_list(raw: object) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(x).strip().lower() for x in raw if str(x).strip()][:6]


def parse_candidates(answer: str, chunk_lineage: str) -> list[Candidate]:
    'Model output → candidates. Anything malformed is dropped, not repaired.'
    match = re.search(r"\[.*\]", answer, re.DOTALL)
    if not match:
        return []
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        log.warning("extractor returned unparseable JSON — dropping the window")
        return []
    if not isinstance(raw, list):
        return []

    out: list[Candidate] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        mtype = str(item.get("type", "")).strip().lower()
        body = str(item.get("body", "")).strip()
        if mtype not in EXTRACT_TYPES or not body:
            continue
        try:
            confidence = float(item.get("confidence", MAX_CONFIDENCE))
        except (TypeError, ValueError):
            confidence = MAX_CONFIDENCE
        out.append(Candidate(
            type=mtype,
            body=body,
            emotion=str(item.get("emotion") or NEUTRALITY_EMOTION_NAME).strip(),
            chunk_lineage=chunk_lineage,
            confidence=max(0.0, min(confidence, MAX_CONFIDENCE)),
            about=_clean_list(item.get("about")),
            tags=_clean_list(item.get("tags")),
            category=(str(item["category"]).strip().lower()
                      if item.get("category") else None),
            item=(str(item["item"]).strip().lower() if item.get("item") else None),
            decision=bool(item.get("decision")),
        ))
    return out


def normalize(candidates: list[Candidate], catalog: list[dict],
              matcher: object | None = None) -> list[Candidate]:
    'Apply Fixes-07 and Fixes-08 before anything reaches the graph.'
    by_name = {str(e["name"]).lower(): str(e["name"]) for e in catalog}
    strays = sorted({c.emotion for c in candidates
                     if c.emotion.lower() not in by_name})
    resolved: dict[str, str] = {}
    if strays and matcher is not None:
        for label, hit in matcher.resolve_many(strays).items():  # type: ignore[attr-defined]
            if hit is not None:
                resolved[label] = hit.name

    kept: list[Candidate] = []
    for c in candidates:
        exact = by_name.get(c.emotion.lower())
        c.emotion = exact or resolved.get(c.emotion) or NEUTRALITY_EMOTION_NAME

        if c.type == "preference":
            c.category = c.category or PREFERENCE_DEFAULT_CATEGORY
            c.item = slugify_item(c.item or c.body)
        kept.append(c)
    return kept


class ConversationExtractor:
    'Turn windows in, grounded candidates out.'

    def __init__(self, llm: object, catalog: list[dict],
                 matcher: object | None = None) -> None:
        self._llm = llm
        self.catalog = catalog
        self.matcher = matcher

    def _catalog_listing(self) -> str:
        by_family: dict[str, list[str]] = {}
        for e in self.catalog:
            by_family.setdefault(str(e.get("family", "")), []).append(str(e["name"]))
        return "\n".join(f"  {fam}: {', '.join(sorted(names))}"
                         for fam, names in sorted(by_family.items()))

    def from_window(self, window_text: str, chunk_lineage: str) -> list[Candidate]:
        'Everything durable in one turn window, grounded to its chunk.'
        if _SELF_RECALL.search(window_text):
            log.info("window %s looks like self-recall — minting nothing",
                     chunk_lineage[:13])
            return []
        prompt = PROMPT.format(catalog=self._catalog_listing(), window=window_text)
        answer = self._llm.complete(prompt)  # type: ignore[attr-defined]
        return normalize(parse_candidates(answer, chunk_lineage), self.catalog,
                         self.matcher)


def write_candidates(
    candidates: list[Candidate], *, writer: object, scope: str,
) -> list[dict]:
    'Mint each candidate and ground it to the turn it came from.'
    written: list[dict] = []
    for c in candidates:
        try:
            lineage = writer.write_memory(c.payload(scope))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - one bad candidate, not the batch
            log.warning("candidate not written: %s", exc)
            written.append({"body": c.body, "error": str(exc)})
            continue
        row = {"lineage_id": lineage, "type": c.type, "body": c.body,
               "emotion": c.emotion, "grounded_to": c.chunk_lineage}
        try:
            writer.ground(lineage, c.chunk_lineage)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            log.error("candidate %s could not be grounded: %s", lineage[:13], exc)
            row["grounding_error"] = str(exc)
        written.append(row)
    return written
