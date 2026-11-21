from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_harness.config import get_settings  # noqa: E402
from memory_harness.db.connection import open_writer  # noqa: E402
from memory_harness.db.migrate import apply_migrations  # noqa: E402
from memory_harness.domain.identity import register_person  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Register a person identity + aliases.")
    ap.add_argument("name", help="display name")
    ap.add_argument("--pronouns", default=None)
    ap.add_argument("--description", default=None)
    ap.add_argument("--alias", action="append", default=[], help="extra alias (repeatable)")
    ap.add_argument("--db", default=None, help="SQLite db path (default: from config)")
    args = ap.parse_args()

    con = open_writer(args.db or get_settings().db.path)
    apply_migrations(con)
    pid = register_person(
        con, args.name, pronouns=args.pronouns,
        description=args.description, aliases=args.alias,
    )
    con.commit()
    con.close()
    print(f"registered {pid} ({args.name})")


if __name__ == "__main__":
    main()
