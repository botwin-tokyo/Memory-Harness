'One todo day snapshot, normalised.'

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class TodoRefused(ValueError):
    'The payload is not something we will accept.'


@dataclass
class TodoItem:
    id: str
    text: str
    position: int
    completed: bool = False
    completed_at: str = ""
    updated_at: str = ""

    @property
    def key_part(self) -> str:
        return self.id


@dataclass
class TodoDay:
    date: str
    todo_day_id: str = ""
    items: list[TodoItem] = field(default_factory=list)
    deleted: bool = False
    deleted_at: str = ""
    created_at: str = ""
    updated_at: str = ""
    source: str = ""

    @property
    def ordered(self) -> list[TodoItem]:
        "The source's canonical ordering: (position, id)."
        return sorted(self.items, key=lambda i: (i.position, i.id))

    @property
    def done_count(self) -> int:
        return sum(1 for i in self.items if i.completed)

    def hub_body(self) -> str:
        'The hub alone must answer "what was on the list that day".'
        lines = [f"[todo] {self.date} — {self.done_count}/{len(self.items)} done"]
        for item in self.ordered:
            mark = "✓" if item.completed else "·"
            lines.append(f"{mark} {item.text}")
        return "\n".join(lines)


def _parse_item(raw: Any, date: str) -> TodoItem:
    if not isinstance(raw, dict):
        raise TodoRefused(f"an item is an object, got {type(raw).__name__}")
    item_id = str(raw.get("id") or "").strip()
    text = str(raw.get("text") or "").strip()
    if not item_id:
        raise TodoRefused("every item needs its id — it is the item's identity")
    if not text:
        raise TodoRefused(f"item {item_id} has no text")
    completed = bool(raw.get("completed"))
    completed_at = str(raw.get("completed_at") or "").strip()
    if not completed:
        completed_at = ""
    try:
        position = int(raw.get("position") or 0)
    except (TypeError, ValueError):
        position = 0
    item_date = str(raw.get("date") or "").strip()
    if item_date and item_date != date:
        raise TodoRefused(
            f"item {item_id} claims date {item_date} inside day {date}")
    return TodoItem(
        id=item_id, text=text, position=position, completed=completed,
        completed_at=completed_at,
        updated_at=str(raw.get("updated_at") or "").strip(),
    )


def parse_todo_day(payload: dict[str, Any]) -> TodoDay:
    'Source payload → a normalised day. Raises `TodoRefused`.'
    if not isinstance(payload, dict):
        raise TodoRefused("payload must be an object")
    kind = str(payload.get("kind") or "todo_day").strip()
    if kind != "todo_day":
        raise TodoRefused(f"kind {kind!r} is not 'todo_day'")
    date = str(payload.get("date") or "").strip()
    if not _DATE.match(date):
        raise TodoRefused(f"date {date!r} is not YYYY-MM-DD — it is the identity")

    raw_items = payload.get("items")
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        raise TodoRefused("items must be a list")

    return TodoDay(
        date=date,
        todo_day_id=str(payload.get("todo_day_id") or "").strip(),
        items=[_parse_item(r, date) for r in raw_items],
        deleted=bool(payload.get("deleted")),
        deleted_at=str(payload.get("deleted_at") or "").strip(),
        created_at=str(payload.get("created_at") or "").strip(),
        updated_at=str(payload.get("updated_at") or "").strip(),
        source=str(payload.get("source") or "").strip(),
    )
