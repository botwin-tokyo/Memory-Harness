from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_harness.clients.embeddings import (  # noqa: E402
    DimensionMismatch,
    EmbeddingsClient,
    EmbeddingsUnavailable,
)
from memory_harness.config import get_settings  # noqa: E402
from memory_harness.db import meta  # noqa: E402
from memory_harness.db.connection import open_writer  # noqa: E402
from memory_harness.db.migrate import apply_migrations, applied_versions  # noqa: E402
from memory_harness.time import now_ms  # noqa: E402

TEMPLATE = Path(__file__).resolve().parents[1] / "memory_harness" / "migrations" / "0004_vectors.sql.tmpl"
VEC_MIGRATION_VERSION = 4


def bootstrap_vectors(con: sqlite3.Connection, client: EmbeddingsClient) -> dict[str, str]:
    "Create the vec table at the endpoint's real dimension. Idempotent."
    health = client.health()
    if health.get("status") != "ok":
        raise EmbeddingsUnavailable(f"embedding service status: {health.get('status')}")
    dim = int(health["dimensions"])
    if dim != client._dim:  # noqa: SLF001 — the client carries the configured dim
        raise DimensionMismatch(f"health dim {dim} != configured {client._dim}")

    apply_migrations(con)
    cur = con.cursor()
    meta.set(cur, "embed_model_id", str(health["model"]))
    meta.set(cur, "embed_dim", str(dim))
    if VEC_MIGRATION_VERSION not in applied_versions(con):
        sql = TEMPLATE.read_text(encoding="utf-8").replace("{{DIM}}", str(dim))
        con.executescript(
            "BEGIN IMMEDIATE;\n"
            + sql
            + f"\nINSERT INTO schema_migrations(version, applied_at)"
            f" VALUES ({VEC_MIGRATION_VERSION}, {now_ms()});\nCOMMIT;"
        )
    con.commit()
    return {"embed_model_id": str(health["model"]), "embed_dim": str(dim)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Bootstrap the vec0 table from the Embedding service.")
    ap.add_argument("--db", default=None, help="SQLite db path (default: from config)")
    args = ap.parse_args()

    settings = get_settings()
    client = EmbeddingsClient(
        settings.embeddings.base_url,
        settings.embeddings.api_key,
        timeout_s=settings.embeddings.timeout_s,
        dim=settings.embeddings.dim,
        max_batch=settings.embeddings.max_batch,
    )
    con = open_writer(args.db or settings.db.path)
    try:
        values = bootstrap_vectors(con, client)
        print(f"vec bootstrapped: {values}")
    except (EmbeddingsUnavailable, DimensionMismatch) as e:
        print(f"bootstrap failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()
        client.close()


if __name__ == "__main__":
    main()
