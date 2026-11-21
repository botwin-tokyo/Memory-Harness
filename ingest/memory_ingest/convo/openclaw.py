'OpenClaw conversations, read from its append-only JSONL transcripts.'

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Iterator

from memory_ingest.convo.model import Conversation, Cursor, Turn

log = logging.getLogger("memory_ingest.convo.openclaw")

_ROLE_MAP = {
    "user": "user",
    "assistant": "assistant",
    "toolresult": "tool", "tool_result": "tool", "tool": "tool",
    "system": "system",
}

_TURN_TYPES = ("message", "custom_message")


def default_sessions_root() -> Path:
    state = os.environ.get("OPENCLAW_STATE_DIR")
    if not state:
        raise ValueError("Supply a conversation source path or configure the adapter directory")
    base = Path(state).expanduser()
    return base / "agents"


def parse_session_filename(path: Path) -> tuple[str, str | None]:
    '`<SessionId>[-topic-<threadId>].jsonl` → `(session_id, thread_id)`.'
    stem = path.stem
    if "-topic-" in stem:
        session, _, thread = stem.partition("-topic-")
        return session, thread
    return stem, None


def _content_text(raw: object) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in ("text", "content", "value"):
            if isinstance(raw.get(key), str):
                return str(raw[key])
        return json.dumps(raw, ensure_ascii=False)
    if isinstance(raw, list):
        return "\n".join(p for p in (_content_text(item) for item in raw) if p)
    return str(raw)


class OpenClawAdapter:
    "Read-only reader over one agent's session transcripts."

    source = "openclaw"

    def __init__(self, sessions_dir: str | Path | None = None, *,
                 agent: str | None = None) -> None:
        self.sessions_dir = Path(sessions_dir).expanduser() if sessions_dir else None
        self.agent = agent


    def session_files(self) -> list[Path]:
        'Every transcript under the configured dir, or under every agent.'
        if self.sessions_dir:
            root = self.sessions_dir
            if root.is_dir() and (root / "sessions").is_dir():
                root = root / "sessions"
            return sorted(root.glob("*.jsonl")) if root.is_dir() else []
        agents = default_sessions_root()
        if not agents.is_dir():
            return []
        return sorted(p for a in agents.iterdir() if a.is_dir()
                      for p in (a / "sessions").glob("*.jsonl"))


    @staticmethod
    def _entries(path: Path, start: int) -> tuple[list[dict], int, dict | None]:
        'Entries from `start` onward, the new offset, and the header.'
        size = path.stat().st_size
        if start > size:
            log.warning("%s shrank (%d < %d) — re-reading from the top",
                        path.name, size, start)
            start = 0

        header: dict | None = None
        entries: list[dict] = []
        with path.open("rb") as fh:
            fh.seek(start)
            raw = fh.read()
            offset = start + len(raw)
            text = raw.decode("utf-8", errors="replace")
            if text and not text.endswith("\n"):
                cut = text.rfind("\n")
                if cut == -1:
                    return [], start, None
                offset = start + len(text[: cut + 1].encode("utf-8"))
                text = text[: cut + 1]

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                log.warning("%s: skipping unparseable line", path.name)
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("type") == "session":
                header = obj
                continue
            entries.append(obj)
        return entries, offset, header

    @staticmethod
    def _active_path(entries: list[dict]) -> list[dict]:
        'The branch that was actually live, newest entry back to the root.'
        if not entries:
            return []
        by_id = {str(e.get("id")): e for e in entries if e.get("id") is not None}
        if not by_id:
            return entries

        path: list[dict] = []
        seen: set[str] = set()
        node: dict | None = entries[-1]
        while node is not None:
            nid = str(node.get("id"))
            if nid in seen:
                log.warning("cycle at entry %s — stopping the walk", nid)
                break
            seen.add(nid)
            path.append(node)
            parent = node.get("parentId")
            node = by_id.get(str(parent)) if parent is not None else None
        path.reverse()
        return path

    def _turn_from(self, entry: dict) -> Turn | None:
        etype = str(entry.get("type") or "message")
        if etype not in _TURN_TYPES:
            return None
        role_raw = str(entry.get("role") or entry.get("sender") or "user")
        role = _ROLE_MAP.get(role_raw.lower().replace(" ", ""), "system")
        content = _content_text(entry.get("content"))
        tool_name = entry.get("toolName") or entry.get("tool_name")
        return Turn(
            role=role,
            content=content,
            ts=float(entry["timestamp"]) if isinstance(
                entry.get("timestamp"), (int, float)) else None,
            tool_name=str(tool_name) if tool_name else None,
            tool_call_id=str(entry["toolCallId"]) if entry.get("toolCallId") else None,
            source_id=str(entry.get("id") or ""),
        )


    def read_file(self, path: Path, cursor: Cursor = "") -> Conversation | None:
        'One transcript from `cursor` on, or None when it has nothing new.'
        session_id, thread = parse_session_filename(path)
        start = int(cursor or 0)
        entries, offset, header = self._entries(path, start)
        if not entries:
            return None

        ordered = self._active_path(entries)
        turns = [t for t in (self._turn_from(e) for e in ordered)
                 if t is not None and not t.is_empty]

        customs = [e for e in entries if str(e.get("type")) == "custom"]
        meta: dict[str, str] = {"file": path.name}
        if customs:
            meta["custom_entries"] = str(len(customs))
        if thread:
            meta["thread_id"] = thread
        if header:
            if header.get("cwd"):
                meta["cwd"] = str(header["cwd"])
            if header.get("timestamp") is not None:
                meta["header_timestamp"] = str(header["timestamp"])

        if not turns:
            return None

        started = header.get("timestamp") if header else None
        return Conversation(
            source=self.source,
            session_id=session_id if not thread else f"{session_id}-topic-{thread}",
            agent=self.agent or (path.parent.parent.name if path.parent.parent else None),
            started_at=float(started) if isinstance(started, (int, float)) else None,
            title=str(header.get("title")) if header and header.get("title") else None,
            turns=turns,
            cursor=str(offset),
            metadata=meta,
        )

    def iter_conversations(
        self, cursors: dict[str, Cursor] | None = None
    ) -> Iterator[Conversation]:
        cursors = cursors or {}
        for path in self.session_files():
            session_id, thread = parse_session_filename(path)
            key = session_id if not thread else f"{session_id}-topic-{thread}"
            convo = self.read_file(path, cursors.get(key, ""))
            if convo is not None:
                yield convo
