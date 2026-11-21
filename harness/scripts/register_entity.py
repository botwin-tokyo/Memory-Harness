from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_harness.config import get_settings  # noqa: E402
from memory_harness.db.connection import open_writer  # noqa: E402
from memory_harness.db.migrate import apply_migrations  # noqa: E402
from memory_harness.domain.identity import ENTITY_KINDS, register_entity  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Register a registry entity.")
    ap.add_argument("kind", choices=ENTITY_KINDS)
    ap.add_argument("name", help="display name")
    ap.add_argument("--slug", default=None, help="canonical slug (default: from name)")
    ap.add_argument("--description", default=None)
    ap.add_argument("--pronouns", default=None)
    ap.add_argument("--aliases", nargs="*", default=[])
    ap.add_argument("--db", default=None, help="db path (default from config)")
    args = ap.parse_args()

    db_path = args.db or get_settings().db.path
    con = open_writer(db_path)
    apply_migrations(con)
    eid = register_entity(
        con,
        args.kind,
        args.name,
        slug=args.slug,
        pronouns=args.pronouns,
        description=args.description,
        aliases=args.aliases,
    )
    con.commit()
    con.close()
    print(eid)


if __name__ == "__main__":
    main()
