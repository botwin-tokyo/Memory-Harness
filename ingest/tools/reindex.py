from __future__ import annotations

import argparse
import sys

import httpx


def main() -> None:
    ap = argparse.ArgumentParser(description="Rebuild + ingest a repo INDEX.")
    ap.add_argument("repos", nargs="+", help="owner/name or github url")
    ap.add_argument("--base", default="http://127.0.0.1:8100")
    args = ap.parse_args()

    failed = False
    for repo in args.repos:
        try:
            r = httpx.post(f"{args.base}/git/index", json={"repo": repo},
                           timeout=3600.0)
            r.raise_for_status()
            o = r.json()
            state = "no-op (sha unchanged)" if o["deduped"] else (
                f"rev {o['revision']}, {o['files']} files, "
                f"{o['summarized']} freshly summarized"
            )
            print(f"{repo}: {state} @ {o['sha'][:10]}")
        except httpx.HTTPError as exc:
            detail = ""
            if isinstance(exc, httpx.HTTPStatusError):
                detail = f" — {exc.response.text[:200]}"
            print(f"{repo}: FAILED {exc}{detail}", file=sys.stderr)
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
