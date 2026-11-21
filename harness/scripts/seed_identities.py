from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_harness.config import get_settings  # noqa: E402
from memory_harness.db.connection import open_writer  # noqa: E402
from memory_harness.db.migrate import apply_migrations  # noqa: E402
from memory_harness.domain.identity import seed_identities  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed the reserved identities (self/companion).")
    ap.add_argument("--db", default=None, help="SQLite db path (default: from config)")
    ap.add_argument("--data", default=None, help="identities JSON (default: repo data/)")
    args = ap.parse_args()

    db_path = args.db or get_settings().db.path
    con = open_writer(db_path)
    applied = apply_migrations(con)
    n = seed_identities(con, args.data)
    con.commit()
    con.close()
    print(f"migrations applied: {applied or 'none pending'}; seeded {n} identities into {db_path}")


if __name__ == "__main__":
    main()
