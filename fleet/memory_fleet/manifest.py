'Build a source manifest from a Collector.'

from __future__ import annotations

from typing import Any

from memory_fleet.collector import Collector
from memory_fleet.design import (
    ID_PATTERN,
    PAYLOAD_SCHEMAS,
    PUBLISHED_KINDS,
    manifest_schema_key,
)
import re

_ID_RE = re.compile(ID_PATTERN)

MAX_LABELS = 5


def build(
    node_id: str,
    source_id: str,
    collector: Collector,
    *,
    expected_heartbeat_s: int,
    required: bool = True,
) -> dict[str, Any]:
    'The manifest dict for `propose_source`.'
    for name in collector.check_names:
        if not _ID_RE.match(name):
            raise ValueError(f"check name {name!r} does not match {ID_PATTERN}")

    metrics: dict[str, Any] = {}
    for name, (unit, kind) in sorted(collector.metric_declarations.items()):
        if not _ID_RE.match(name):
            raise ValueError(f"metric name {name!r} does not match {ID_PATTERN}")
        if kind not in ("gauge", "counter"):
            raise ValueError(f"metric {name!r} kind {kind!r} must be gauge or counter")
        metrics[name] = {"unit": unit, "kind": kind, "labels": []}

    schemas = {
        manifest_schema_key(node_id, source_id, kind): PAYLOAD_SCHEMAS[kind]
        for kind in PUBLISHED_KINDS
    }

    return {
        "node_id": node_id,
        "source_id": source_id,
        "transport": "push",
        "expected_heartbeat_s": expected_heartbeat_s,
        "required": required,
        "schemas": schemas,
        "metrics": metrics,
        "checks": list(collector.check_names),
        "endpoint_ref": None,
        "mapping": {},
        "subscribe": None,
        "delivery_capability": "replay",
    }


def declare_labels(manifest: dict[str, Any], metric: str, labels: list[str]) -> None:
    'Attach a label set to an already-built metric declaration.'
    if metric not in manifest["metrics"]:
        raise KeyError(f"{metric} is not declared in this manifest")
    if len(labels) > MAX_LABELS:
        raise ValueError(f"{metric}: {len(labels)} labels exceeds the cap of {MAX_LABELS}")
    for label in labels:
        if not _ID_RE.match(label):
            raise ValueError(f"label {label!r} does not match {ID_PATTERN}")
    manifest["metrics"][metric]["labels"] = list(labels)
