from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")


class WriteQueue:
    'Serializes all writes onto the one write connection.'

    def __init__(self, con: sqlite3.Connection) -> None:
        self._con = con
        self._lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-writer")

    async def execute(self, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        'Run one statement (auto-committed) and return any result rows.'
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._executor, self._execute_sync, sql, params)

    def _execute_sync(self, sql: str, params: tuple[object, ...]) -> list[sqlite3.Row]:
        cur = self._con.execute(sql, params)
        return cur.fetchall()

    async def tx(self, fn: Callable[[sqlite3.Cursor], T]) -> T:
        'Run `fn(cursor)` inside one serialized IMMEDIATE transaction.'
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._executor, self._tx_sync, fn)

    def _tx_sync(self, fn: Callable[[sqlite3.Cursor], T]) -> T:
        cur = self._con.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            result = fn(cur)
            self._con.commit()
            return result
        except BaseException:
            self._con.rollback()
            raise

    def close(self) -> None:
        self._executor.shutdown(wait=True)
        self._con.close()
