"The hub's wire contract, pinned."

from __future__ import annotations

from typing import Any, Final

PROTOCOL_VERSION: Final = 1
DESIGN_REVISION: Final = "v1-bearer-2"
CONTRACT_DIGEST: Final = "fe51db760ae1e5e3591d0e91dc8f4fab0acb092bec4cab8889c761936e1e9e15"

INGEST_WSS_URL: Final = ""

EVENT_BYTES_MAX: Final = 65_536
FRAME_BYTES_MAX: Final = 262_144
BATCH_EVENTS_MAX: Final = 100

KIND_HEARTBEAT: Final = "heartbeat"
KIND_HEALTH: Final = "health"
KIND_METRIC: Final = "metric"
KIND_LIFECYCLE: Final = "lifecycle"
PUBLISHED_KINDS: Final = (KIND_HEARTBEAT, KIND_HEALTH, KIND_METRIC, KIND_LIFECYCLE)

ID_PATTERN: Final = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$"
ID_MAX_LEN: Final = 64

HEARTBEAT_MIN_S: Final = 5
HEARTBEAT_MAX_S: Final = 3600

HEALTH_STATUSES: Final = ("healthy", "degraded", "unhealthy", "unknown")
LIFECYCLE_STATES: Final = ("starting", "ready", "stopping", "stopped")

PAYLOAD_SCHEMAS: Final[dict[str, dict[str, Any]]] = {
    "heartbeat": {
        "type": "object",
        "properties": {
            "uptime_s": {"type": "number", "minimum": 0},
            "publisher_version": {"type": "string", "maxLength": 4096},
            "spool_depth": {"type": "integer", "minimum": 0},
            "last_error": {"type": "string", "maxLength": 4096},
        },
        "required": ["uptime_s", "publisher_version"],
        "additionalProperties": False,
    },
    "health": {
        "type": "object",
        "properties": {
            "status": {"enum": list(HEALTH_STATUSES)},
            "summary": {"type": "string", "maxLength": 4096},
            "checks": {
                "type": "array",
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "maxLength": 4096},
                        "status": {"enum": list(HEALTH_STATUSES)},
                    },
                    "required": ["name", "status"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["status", "summary", "checks"],
        "additionalProperties": False,
    },
    "metric": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "maxLength": 4096},
            "value": {"type": "number"},
            "unit": {"type": "string", "maxLength": 4096},
            "labels": {
                "type": "object",
                "maxProperties": 5,
                "additionalProperties": {"type": "string", "maxLength": 128},
            },
        },
        "required": ["name", "value", "unit", "labels"],
        "additionalProperties": False,
    },
    "lifecycle": {
        "type": "object",
        "properties": {
            "state": {"enum": list(LIFECYCLE_STATES)},
            "reason": {"type": "string", "maxLength": 4096},
        },
        "required": ["state", "reason"],
        "additionalProperties": False,
    },
}

EVENT_REQUIRED: Final = (
    "protocol_version", "event_id", "node_id", "source_id", "boot_id", "seq",
    "kind", "schema_id", "schema_version", "observed_at", "payload",
)

PAYLOAD_SCHEMA_VERSION: Final = "1.0.0"


def manifest_schema_key(node_id: str, source_id: str, kind: str) -> str:
    return f"{node_id}/{source_id}.{kind}@{PAYLOAD_SCHEMA_VERSION}"


def schema_id(node_id: str, source_id: str, kind: str) -> str:
    return f"{node_id}/{source_id}.{kind}"
