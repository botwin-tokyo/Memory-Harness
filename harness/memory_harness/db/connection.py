from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlite_vec


def open_writer(path: str | Path, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    'Open THE write connection. There must only ever be one per process.'
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    return con


def open_reader(path: str | Path, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    'Open a read-only connection (WAL snapshot reads never block the writer).'
    con = sqlite3.connect(
        f"file:{path}?mode=ro", uri=True, isolation_level=None, check_same_thread=False
    )
    con.row_factory = sqlite3.Row
    con.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    return con
