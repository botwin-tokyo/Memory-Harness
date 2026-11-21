from __future__ import annotations

import argparse
import secrets as pysecrets
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root

import httpx  # noqa: E402

from memory_harness.config import get_settings  # noqa: E402
from memory_harness.db import meta  # noqa: E402
from memory_harness.db.connection import open_writer  # noqa: E402


def _scan_feed_for_secrets(items: list[dict], planted: list[str]) -> list[str]:
    """Any planted secret string visible anywhere in the feed items = a leak."""
    import json as _json

    blob = _json.dumps(items)
    return [s for s in planted if s in blob]


def verify_secret_gate(client: httpx.Client, token: str, con: sqlite3.Connection) -> bool:
    """Plant, check, and (only if clean) set meta.secret_scan_verified=1."""
    h = {"Authorization": f"Bearer {token}"}
    aws_key = "AKIA" + "".join(
        pysecrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(16)
    )
    hex_key = pysecrets.token_hex(32)
    credential_value = f"credential-{pysecrets.token_hex(16)}"
    planted = [aws_key, hex_key, credential_value]


    r = client.post("/v1/events", json={
        "event_type": "tool_result",
        "content": f"the dump printed {aws_key} and api_key={hex_key}",
        "idempotency_key": f"gate-{pysecrets.token_hex(4)}",
    }, headers=h)
    assert r.status_code == 200, r.text
    r = client.post("/v1/remember", json={
        "type": "fact", "body": f"the old key was {hex_key}", "emotion": "Neutrality",
    }, headers=h)
    assert r.status_code == 200, r.text
    r = client.post("/v1/remember", json={
        "type": "credential", "body": credential_value, "emotion": "Neutrality",
    }, headers=h)
    assert r.status_code == 200, r.text

    feed = client.get("/v1/changes", params={"since": 0, "learning_eligible": True},
                      headers=h)
    assert feed.status_code == 200, feed.text
    leaks = _scan_feed_for_secrets(feed.json()["items"], planted)
    if leaks:
        print(f"SECRET GATE FAILED: {len(leaks)} planted secret(s) in the learning feed")
        return False


    full = client.get("/v1/changes", params={"since": 0}, headers=h)
    redacted_items = [i for i in full.json()["items"] if i["redacted"]]
    if not redacted_items:
        print("SECRET GATE FAILED: no redacted capture found in the raw feed")
        return False
    raw_leaks = _scan_feed_for_secrets(
        [i for i in full.json()["items"] if i["redacted"]], [aws_key, hex_key]
    )
    if raw_leaks:
        print("SECRET GATE FAILED: raw secret visible in a redacted item")
        return False

    meta.set(con.cursor(), "secret_scan_verified", "1")
    con.commit()
    print("secret gate verified: learning feed is clean; meta.secret_scan_verified=1")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify the capture-time secret gate.")
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--db", default=None)
    ap.add_argument("--key-file", required=True)
    args = ap.parse_args()

    settings = get_settings()
    token = ""
    for line in Path(args.key_file).read_text().splitlines():
        if line.startswith("raw key"):
            token = line.split(": ", 1)[1].strip()
    if not token:
        print(f"no raw key found in {args.key_file}", file=sys.stderr)
        sys.exit(1)

    con = open_writer(args.db or settings.db.path)
    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        ok = verify_secret_gate(client, token, con)
    con.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
