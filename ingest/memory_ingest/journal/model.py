'One journal entry, normalised.'

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from memory_contract import JOURNAL_METRICS, JOURNAL_SECTIONS, JOURNAL_TYPES

_ALIASES = {
    "dayOfWeek": "day_of_week", "journalType": "journal_type",
    "generatedSummary": "generated_summary", "statusTags": "status_tags",
    "overallTone": "overall_tone", "dailySnapshot": "daily_snapshot",
    "intentFocus": "intent_focus", "risksFriction": "risks_friction",
    "actionsCommitments": "actions_commitments", "winsProgress": "wins_progress",
    "emotionalContext": "emotional_context", "tomorrowSetup": "tomorrow_setup",
    "createdAt": "created_at", "updatedAt": "updated_at",
}


class JournalRefused(ValueError):
    'The payload is not something we will accept.'


@dataclass
class JournalEntry:
    date: str
    journal_type: str
    headline: str = ""
    generated_summary: str = ""
    day_of_week: str = ""
    status_tags: list[str] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=dict)
    overall_tone: str = ""
    sections: dict[str, str] = field(default_factory=dict)
    source_id: str = ""
    updated_at: str = ""

    @property
    def emotional_context(self) -> str:
        return self.sections.get("emotional_context", "")

    def hub_body(self) -> str:
        lines = [f"[journal] {self.date}"]
        if self.day_of_week:
            lines[0] += f" ({self.day_of_week})"
        if self.headline:
            lines.append(self.headline)
        if self.generated_summary:
            lines.append("")
            lines.append(self.generated_summary)
        return "\n".join(lines)


def _render_section(value: Any) -> str:
    'A section object → prose.'
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(f"- {x}" for x in value if str(x).strip())
    if not isinstance(value, dict):
        return str(value).strip()

    parts: list[str] = []
    if value.get("summary"):
        parts.append(str(value["summary"]).strip())
    if value.get("text"):
        parts.append(str(value["text"]).strip())
    items = value.get("items") or []
    if items:
        parts.append("\n".join(f"- {x}" for x in items if str(x).strip()))
    todos = value.get("linkedTodos") or value.get("linked_todos") or []
    for todo in todos:
        if not isinstance(todo, dict):
            continue
        mark = "x" if todo.get("completed") else " "
        parts.append(f"- [{mark}] {todo.get('text', '')}".rstrip())
    return "\n\n".join(p for p in parts if p)


def parse(payload: dict[str, Any]) -> JournalEntry:
    'Source payload → a normalised entry. Raises `JournalRefused`.'
    if not isinstance(payload, dict):
        raise JournalRefused("payload must be an object")
    data = {_ALIASES.get(k, k): v for k, v in payload.items()}

    date = str(data.get("date") or "").strip()
    if not date:
        raise JournalRefused("date is required — it is half the identity")
    jtype = str(data.get("journal_type") or "full-day").strip()
    if jtype not in JOURNAL_TYPES:
        raise JournalRefused(
            f"journal_type {jtype!r} is not one of {JOURNAL_TYPES}")

    metrics: dict[str, int] = {}
    for name in JOURNAL_METRICS:
        raw = data.get(name)
        if raw is None:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= value <= 5:
            metrics[name] = value

    sections: dict[str, str] = {}
    for name in JOURNAL_SECTIONS:
        raw = data.get(name)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                pass
        rendered = _render_section(raw)
        if rendered:
            sections[name] = rendered

    tags = data.get("status_tags") or []
    return JournalEntry(
        date=date,
        journal_type=jtype,
        headline=str(data.get("headline") or "").strip(),
        generated_summary=str(data.get("generated_summary") or "").strip(),
        day_of_week=str(data.get("day_of_week") or "").strip(),
        status_tags=[str(t) for t in tags if str(t).strip()],
        metrics=metrics,
        overall_tone=str(data.get("overall_tone") or "").strip(),
        sections=sections,
        source_id=str(data.get("id") or "").strip(),
        updated_at=str(data.get("updated_at") or "").strip(),
    )
