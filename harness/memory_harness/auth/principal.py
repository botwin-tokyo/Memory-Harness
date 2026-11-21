from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3

from memory_harness import ids
from memory_harness.models.domain import Principal
from memory_harness.time import now_ms

VALID_CEILINGS = ("user", "trusted_tool", "agent")
VALID_LINK_AUTHORITY = ("active", "propose")
VALID_SECRET_MODES = ("store-but-dont-train", "train-raw")


def hash_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_key(
    con: sqlite3.Connection,
    principal_id: str,
    node: str,
    allowed_scopes: list[str],
    trust_ceiling: str = "agent",
    home_scope: str | None = None,
    learning_eligible_default: bool = True,
    secret_mode: str = "store-but-dont-train",
    link_authority: str = "propose",
    read_only: bool = False,
    private_authority: bool = False,
) -> tuple[str, str]:
    'Insert a policy row and return (raw_token, principal_key_id).'
    if trust_ceiling not in VALID_CEILINGS:
        raise ValueError(f"trust_ceiling must be one of {VALID_CEILINGS}")
    if link_authority not in VALID_LINK_AUTHORITY:
        raise ValueError(f"link_authority must be one of {VALID_LINK_AUTHORITY}")
    if secret_mode not in VALID_SECRET_MODES:
        raise ValueError(f"secret_mode must be one of {VALID_SECRET_MODES}")
    token = secrets.token_hex(32)
    key_id = ids.uuid7()
    con.execute(
        "INSERT INTO policy(principal_key_id, key_hash, principal_id, node,"
        " allowed_scopes, trust_ceiling, home_scope, learning_eligible_default,"
        " secret_mode, link_authority, read_only, private_authority,"
        " created_at, revoked_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            key_id,
            hash_key(token),
            principal_id,
            node,
            json.dumps(allowed_scopes),
            trust_ceiling,
            home_scope or f"private:{principal_id}",
            1 if learning_eligible_default else 0,
            secret_mode,
            link_authority,
            1 if read_only else 0,
            1 if private_authority else 0,
            now_ms(),
        ),
    )
    return token, key_id


def revoke_key(con: sqlite3.Connection, principal_key_id: str) -> bool:
    'Set revoked_at on a policy row. Returns True if a row was revoked.'
    cur = con.execute(
        "UPDATE policy SET revoked_at = ? WHERE principal_key_id = ? AND revoked_at IS NULL",
        (now_ms(), principal_key_id),
    )
    return cur.rowcount > 0


def resolve(con: sqlite3.Connection, token: str) -> Principal | None:
    'Hash the bearer token -> non-revoked policy row -> full Principal.'
    row = con.execute(
        "SELECT * FROM policy WHERE key_hash = ? AND revoked_at IS NULL",
        (hash_key(token),),
    ).fetchone()
    if row is None:
        return None
    return Principal(
        principal_key_id=str(row["principal_key_id"]),
        principal_id=str(row["principal_id"]),
        node=str(row["node"]),
        allowed_scopes=[str(s) for s in json.loads(str(row["allowed_scopes"]))],
        trust_ceiling=str(row["trust_ceiling"]),
        home_scope=str(row["home_scope"]),
        learning_eligible_default=bool(row["learning_eligible_default"]),
        secret_mode=str(row["secret_mode"]),
        link_authority=str(row["link_authority"]),
        private_authority=bool(row["private_authority"])
        if "private_authority" in row.keys() else False,
        read_only=bool(row["read_only"]) if "read_only" in row.keys() else False,
    )
