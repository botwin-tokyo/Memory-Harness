'Delete index rows that point at memories the graph no longer holds.'

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_harness.config import get_settings  # noqa: E402
from memory_harness.curator.canary import _INDEXED  # noqa: E402
from memory_harness.db.connection import open_reader, open_writer  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    con = open_reader(get_settings().db.path)
    have_vec = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vec'"
    ).fetchone() is not None
    found: list[tuple[str, str, str]] = []
    try:
        for kind, table, pk, live in _INDEXED:
            for index in ("fts_memories",) + (("vec",) if have_vec else ()):
                for r in con.execute(
                    f"SELECT ref_id FROM {index} x WHERE x.kind=? AND NOT EXISTS"
                    f" (SELECT 1 FROM {table} t WHERE t.{pk}=x.ref_id AND {live})",
                    (kind,),
                ):
                    found.append((index, kind, str(r["ref_id"])))
    finally:
        con.close()

    print(f"{len(found)} orphaned index rows")
    for index, kind, ref in found[:12]:
        print(f"  {index:14s} {kind:11s} {ref}")
    if len(found) > 12:
        print(f"  … and {len(found) - 12} more")
    if not found:
        return 0
    if not args.apply:
        print("\nDry run. Re-run with --apply to delete them.")
        return 0

    w = open_writer(get_settings().db.path)
    try:
        for index, kind, ref in found:
            w.execute(f"DELETE FROM {index} WHERE kind=? AND ref_id=?", (kind, ref))
        w.commit()
    finally:
        w.close()
    print(f"\ndeleted {len(found)} orphaned index rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
