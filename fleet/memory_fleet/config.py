from __future__ import annotations

import os
import re
from dataclasses import dataclass

from memory_contract.environment import load_environment
from memory_fleet.design import (
    HEARTBEAT_MAX_S,
    HEARTBEAT_MIN_S,
    ID_PATTERN,
    INGEST_WSS_URL,
)

ENV_ENABLED = "MEMORY_FLEET_ENABLED"
ENV_KEY = "MEMORY_FLEET_TELEMETRY_KEY"
ENV_NODE = "MEMORY_FLEET_NODE_ID"
ENV_SOURCE = "MEMORY_FLEET_SOURCE_ID"
ENV_URL = "MEMORY_FLEET_INGEST_URL"
ENV_HEARTBEAT = "MEMORY_FLEET_HEARTBEAT_S"
ENV_SPOOL = "MEMORY_FLEET_SPOOL_MAX"

DEFAULT_NODE_ID = "memory-node"
DEFAULT_HEARTBEAT_S = 30
DEFAULT_SPOOL_MAX = 2000

_ID_RE = re.compile(ID_PATTERN)


class ConfigError(ValueError):
    'Misconfiguration that must fail loudly rather than publish somewhere wrong.'


@dataclass(frozen=True)
class FleetConfig:
    node_id: str
    source_id: str
    telemetry_key: str
    ingest_url: str = INGEST_WSS_URL
    heartbeat_s: int = DEFAULT_HEARTBEAT_S
    spool_max: int = DEFAULT_SPOOL_MAX
    sample_every_s: float = 60.0


def _require_id(value: str, what: str) -> str:
    if not _ID_RE.match(value):
        raise ConfigError(f"{what} {value!r} does not match {ID_PATTERN}")
    return value


def from_env(source_id: str, *, env: dict[str, str] | None = None) -> FleetConfig | None:
    'Build the config for `source_id`, or None when publishing is not configured.'
    if env is None:
        load_environment()
    src: dict[str, str] = dict(os.environ) if env is None else env

    key = (src.get(ENV_KEY) or "").strip()
    if not key:
        return None
    if (src.get(ENV_ENABLED) or "1").strip().lower() in {"0", "false", "no"}:
        return None

    node_id = _require_id((src.get(ENV_NODE) or DEFAULT_NODE_ID).strip(), "node_id")
    source_id = _require_id((src.get(ENV_SOURCE) or source_id).strip(), "source_id")

    heartbeat = int(src.get(ENV_HEARTBEAT) or DEFAULT_HEARTBEAT_S)
    if not HEARTBEAT_MIN_S <= heartbeat <= HEARTBEAT_MAX_S:
        raise ConfigError(
            f"heartbeat_s {heartbeat} outside the design's "
            f"{HEARTBEAT_MIN_S}..{HEARTBEAT_MAX_S}"
        )

    spool = int(src.get(ENV_SPOOL) or DEFAULT_SPOOL_MAX)
    if spool < 1:
        raise ConfigError(f"spool_max {spool} must be >= 1")

    url = (src.get(ENV_URL) or INGEST_WSS_URL).strip()
    if not url.startswith("wss://"):
        raise ConfigError(f"ingest url must be wss://, got {url!r}")

    return FleetConfig(
        node_id=node_id,
        source_id=source_id,
        telemetry_key=key,
        ingest_url=url,
        heartbeat_s=heartbeat,
        spool_max=spool,
    )
