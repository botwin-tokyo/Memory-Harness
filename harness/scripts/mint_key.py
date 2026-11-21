from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_harness.auth.principal import (  # noqa: E402
    VALID_CEILINGS,
    VALID_LINK_AUTHORITY,
    VALID_SECRET_MODES,
    mint_key,
)
from memory_harness.config import get_settings  # noqa: E402
from memory_harness.db.connection import open_writer  # noqa: E402
from memory_harness.db.migrate import apply_migrations  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Mint a harness API key (policy row).")
    ap.add_argument("principal_id", help="stable agent identity (provenance)")
    ap.add_argument("node", help="node identifier")
    ap.add_argument("--scopes", nargs="+", required=True, help="allowed scopes")
    ap.add_argument("--ceiling", default="agent", choices=VALID_CEILINGS)
    ap.add_argument("--home", default=None, help="home scope (default: private:<principal_id>)")
    ap.add_argument("--link-authority", default="propose", choices=VALID_LINK_AUTHORITY)
    ap.add_argument("--secret-mode", default="store-but-dont-train", choices=VALID_SECRET_MODES)
    ap.add_argument("--learning-eligible", type=int, default=1, choices=[0, 1])
    ap.add_argument("--private-authority", action="store_true",
                    help="may reach the private store (/v1/private/*). Off by "
                         "default; granting it is deliberate.")
    ap.add_argument("--read-only", action="store_true",
                    help="viewer key: may read, may never mutate memory")
    ap.add_argument("--db", default=None, help="SQLite db path (default: from config)")
    args = ap.parse_args()

    con = open_writer(args.db or get_settings().db.path)
    apply_migrations(con)
    token, key_id = mint_key(
        con,
        principal_id=args.principal_id,
        node=args.node,
        allowed_scopes=args.scopes,
        trust_ceiling=args.ceiling,
        home_scope=args.home,
        learning_eligible_default=bool(args.learning_eligible),
        secret_mode=args.secret_mode,
        link_authority=args.link_authority,
        read_only=args.read_only,
        private_authority=args.private_authority,
    )
    con.commit()
    con.close()
    print(f"principal_key_id: {key_id}")
    print(f"raw key (shown once, store it now): {token}")


if __name__ == "__main__":
    main()
