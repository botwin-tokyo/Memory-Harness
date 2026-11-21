from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from memory_harness.time import now_ms

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

_VERSION_RE = re.compile(r"^(\d+)_.*\.sql$")


def applied_versions(con: sqlite3.Connection) -> set[int]:
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if exists is None:
        return set()
    return {int(r[0]) for r in con.execute("SELECT version FROM schema_migrations")}


def apply_migrations(
    con: sqlite3.Connection, migrations_dir: str | Path | None = None
) -> list[int]:
    'Apply pending migrations in order. Returns newly applied version numbers.'
    mdir = Path(migrations_dir) if migrations_dir is not None else MIGRATIONS_DIR
    done = applied_versions(con)
    newly: list[int] = []
    for path in sorted(mdir.glob("*.sql")):
        m = _VERSION_RE.match(path.name)
        if m is None:
            continue
        version = int(m.group(1))
        if version in done:
            continue
        script = path.read_text(encoding="utf-8")
        try:
            con.executescript(
                "BEGIN IMMEDIATE;\n"
                + script
                + f"\nINSERT INTO schema_migrations(version, applied_at) "
                f"VALUES ({version}, {now_ms()});\nCOMMIT;"
            )
        except BaseException:
            con.rollback()
            raise
        newly.append(version)
    return newly


def schema_version(con: sqlite3.Connection) -> int:
    'Highest applied migration version (0 if none).'
    versions = applied_versions(con)
    return max(versions) if versions else 0
