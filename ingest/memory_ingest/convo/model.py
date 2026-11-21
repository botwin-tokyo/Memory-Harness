'The normalized conversation shape every adapter produces.'

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterator, Protocol

from memory_contract import TURN_ROLES

Cursor = str


@dataclass
class Turn:
    'One thing that was said, by whoever said it.'

    role: str
    content: str
    ts: float | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_calls: str | None = None
    reasoning: str | None = None
    source_id: str = ""

    def __post_init__(self) -> None:
        if self.role not in TURN_ROLES:
            raise ValueError(f"role {self.role!r} not in {TURN_ROLES}")

    @property
    def is_empty(self) -> bool:
        return not (self.content or "").strip()

    def rendered(self) -> str:
        who = self.role if not self.tool_name else f"tool:{self.tool_name}"
        return f"[{who}] {(self.content or '').strip()}"


@dataclass
class Conversation:
    'A whole session, ordered, from one source.'

    source: str
    session_id: str
    turns: list[Turn] = field(default_factory=list)
    agent: str | None = None
    parent_session_id: str | None = None
    participants: list[str] = field(default_factory=list)
    started_at: float | None = None
    ended_at: float | None = None
    model: str | None = None
    title: str | None = None
    project: str | None = None
    cursor: Cursor = ""
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source}·{self.session_id}"

    @property
    def content_sha(self) -> str:
        h = hashlib.sha256()
        for t in self.turns:
            h.update(f"{t.role}\x1f{t.content}\x1f{t.source_id}\x1e".encode())
        return h.hexdigest()

    def header(self) -> str:
        "The hub node's body: what this conversation was, at a glance."
        lines = [f"[conversation] {self.title or self.session_id}",
                 f"source: {self.source}" + (f" · agent: {self.agent}" if self.agent else "")]
        if self.model:
            lines.append(f"model: {self.model}")
        if self.participants:
            lines.append(f"participants: {', '.join(self.participants)}")
        if self.started_at:
            lines.append(f"started_at: {self.started_at}")
        if self.parent_session_id:
            lines.append(f"continues: {self.parent_session_id}")
        counts: dict[str, int] = {}
        for t in self.turns:
            counts[t.role] = counts.get(t.role, 0) + 1
        lines.append("turns: " + (" · ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
                                  or "none"))
        lines.append(f"session: {self.session_id}")
        return "\n".join(lines)


class SourceAdapter(Protocol):
    'What a source has to provide. Read-only, always.'

    source: str

    def iter_conversations(
        self, cursors: dict[str, Cursor] | None = None
    ) -> Iterator[Conversation]:
        'Yield conversations that have new turns since `cursors`.'
        ...
