from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_harness.auth.principal import revoke_key  # noqa: E402
from memory_harness.config import get_settings  # noqa: E402
from memory_harness.db.connection import open_writer  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Revoke a harness API key.")
    ap.add_argument("principal_key_id")
    ap.add_argument("--db", default=None, help="SQLite db path (default: from config)")
    args = ap.parse_args()

    con = open_writer(args.db or get_settings().db.path)
    ok = revoke_key(con, args.principal_key_id)
    con.commit()
    con.close()
    print("revoked" if ok else "no active key with that principal_key_id")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
