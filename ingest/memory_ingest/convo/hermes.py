'Hermes conversations, read out of its own SQLite without disturbing it.'

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Iterator

from memory_ingest.convo.model import Conversation, Cursor, Turn

log = logging.getLogger("memory_ingest.convo.hermes")

_ROLE_MAP = {
    "user": "user", "human": "user",
    "assistant": "assistant", "ai": "assistant", "model": "assistant",
    "tool": "tool", "function": "tool", "tool_result": "tool",
    "system": "system", "developer": "system",
}


def default_db_path() -> Path:
    home = os.environ.get("HERMES_HOME")
    if not home:
        raise ValueError("Supply a conversation source path or configure the adapter directory")
    base = Path(home).expanduser()
    return base / "state.db"


class HermesAdapter:
    'Read-only reader over a Hermes `state.db`.'

    source = "hermes"

    def __init__(self, db_path: str | Path | None = None, *,
                 agent: str | None = None, copy_first: bool = True) -> None:
        self.db_path = Path(db_path).expanduser() if db_path else default_db_path()
        self.agent = agent
        self.copy_first = copy_first
        self._scratch: Path | None = None


    def _snapshot(self) -> Path:
        'Copy the db and its WAL/SHM sidecars so we read a coherent view.'
        tmp = Path(tempfile.mkdtemp(prefix="hermes-snap-"))
        self._scratch = tmp
        target = tmp / self.db_path.name
        shutil.copy2(self.db_path, target)
        for suffix in ("-wal", "-shm"):
            side = Path(str(self.db_path) + suffix)
            if side.is_file():
                shutil.copy2(side, Path(str(target) + suffix))
        return target

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.is_file():
            raise FileNotFoundError(f"no hermes db at {self.db_path}")
        path = self._snapshot() if self.copy_first else self.db_path
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    def close(self) -> None:
        if self._scratch and self._scratch.is_dir():
            shutil.rmtree(self._scratch, ignore_errors=True)
            self._scratch = None


    @staticmethod
    def _columns(con: sqlite3.Connection, table: str) -> set[str]:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}

    def _turn_from(self, row: sqlite3.Row, cols: set[str]) -> Turn:
        def get(name: str) -> object | None:
            return row[name] if name in cols else None

        raw_calls = get("tool_calls")
        calls: str | None = None
        if raw_calls:
            calls = raw_calls if isinstance(raw_calls, str) else json.dumps(raw_calls)

        role = _ROLE_MAP.get(str(row["role"] or "").lower().strip(), "system")
        if get("tool_call_id") and role not in ("tool",):
            role = "tool"

        return Turn(
            role=role,
            content=str(row["content"] or ""),
            ts=float(row["timestamp"]) if get("timestamp") is not None else None,
            tool_name=str(get("tool_name")) if get("tool_name") else None,
            tool_call_id=str(get("tool_call_id")) if get("tool_call_id") else None,
            tool_calls=calls,
            reasoning=str(get("reasoning")) if get("reasoning") else None,
            source_id=str(row["id"]),
        )

    def iter_conversations(
        self, cursors: dict[str, Cursor] | None = None
    ) -> Iterator[Conversation]:
        cursors = cursors or {}
        con = self._connect()
        try:
            scols = self._columns(con, "sessions")
            mcols = self._columns(con, "messages")
            sessions = con.execute(
                "SELECT * FROM sessions ORDER BY started_at, id"
            ).fetchall()

            for s in sessions:
                sid = str(s["id"])
                after = int(cursors.get(sid) or 0)
                rows = con.execute(
                    "SELECT * FROM messages WHERE session_id = ? AND id > ?"
                    " ORDER BY timestamp, id",
                    (sid, after),
                ).fetchall()
                if not rows:
                    continue

                turns = [self._turn_from(r, mcols) for r in rows]
                def col(name: str) -> object | None:
                    return s[name] if name in scols else None

                def num(name: str) -> float | None:
                    v = col(name)
                    return float(v) if isinstance(v, (int, float, str)) and v != "" \
                        else None

                yield Conversation(
                    source=self.source,
                    session_id=sid,
                    agent=self.agent or (str(col("source")) if col("source") else None),
                    parent_session_id=(str(col("parent_session_id"))
                                       if col("parent_session_id") else None),
                    participants=[str(col("user_id"))] if col("user_id") else [],
                    started_at=num("started_at"),
                    ended_at=num("ended_at"),
                    model=str(col("model")) if col("model") else None,
                    title=str(col("title")) if col("title") else None,
                    turns=turns,
                    cursor=str(max(int(r["id"]) for r in rows)),
                    metadata={k: str(col(k)) for k in ("end_reason", "message_count")
                              if col(k) is not None},
                )
        finally:
            con.close()
            self.close()
