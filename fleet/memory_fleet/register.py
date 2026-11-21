from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any

from memory_fleet import manifest as manifest_mod
from memory_fleet.collector import Collector, safe_metrics, safe_snapshot
from memory_fleet.config import DEFAULT_HEARTBEAT_S, DEFAULT_NODE_ID
from memory_fleet.design import (
    CONTRACT_DIGEST,
    DESIGN_REVISION,
    KIND_HEALTH,
    KIND_HEARTBEAT,
    KIND_LIFECYCLE,
    KIND_METRIC,
    PROTOCOL_VERSION,
)
from memory_fleet.mcpclient import HubClient, ToolError

from memory_contract.environment import load_environment
import os
OWNED_SOURCES = {"harness", "ingest", "mcp"}


def assert_design(hub: HubClient) -> dict[str, Any]:
    design = hub.call("get_design")
    if not isinstance(design, dict):
        raise SystemExit(f"get_design returned {type(design).__name__}, want an object")
    problems = []
    if design.get("protocol_version") != PROTOCOL_VERSION:
        problems.append(
            f"protocol_version {design.get('protocol_version')} != pinned {PROTOCOL_VERSION}"
        )
    if design.get("design_revision") != DESIGN_REVISION:
        problems.append(
            f"design_revision {design.get('design_revision')!r} != pinned {DESIGN_REVISION!r}"
        )
    if design.get("contract_digest") != CONTRACT_DIGEST:
        problems.append(
            f"contract_digest {design.get('contract_digest')!r} != pinned {CONTRACT_DIGEST!r}"
        )
    if problems:
        raise SystemExit(
            "the hub's contract has moved since memory_fleet/design.py was pinned:\n  - "
            + "\n  - ".join(problems)
            + "\n\nRe-read get_design and update design.py before registering."
        )
    return design


def load_collector(source: str) -> Collector:
    "Import the owning service's collector."
    if source == "harness":
        from memory_harness.obs.fleet_collector import build_collector
    elif source == "ingest":
        from memory_ingest.fleet_collector import build_collector
    elif source == "mcp":
        from memory_mcp.fleet_collector import build_collector
    else:  # pragma: no cover - guarded by the allowlist before we get here
        raise SystemExit(f"no collector for source {source!r}")
    return build_collector()


def sample_events(node: str, source: str, collector: Collector) -> list[dict[str, Any]]:
    'One real event of each published kind, from the live collector.'
    from memory_fleet.config import FleetConfig
    from memory_fleet.publisher import FleetPublisher

    probe = FleetPublisher(
        FleetConfig(node_id=node, source_id=source, telemetry_key="registration-probe"),
        collector,
    )
    snap = safe_snapshot(collector)
    metrics = safe_metrics(collector)

    events = [
        probe._envelope(KIND_LIFECYCLE, {"state": "starting", "reason": "registration"}),
        probe._heartbeat_event(),
        probe._envelope(
            KIND_HEALTH,
            {
                "status": snap.status,
                "summary": snap.summary,
                "checks": [{"name": c.name, "status": c.status} for c in snap.checks],
            },
        ),
    ]
    for sample in metrics[:17]:
        events.append(
            probe._envelope(
                KIND_METRIC,
                {
                    "name": sample.name,
                    "value": float(sample.value),
                    "unit": sample.unit,
                    "labels": dict(sample.labels),
                },
            )
        )
    return events


def main(argv: list[str] | None = None) -> int:
    load_environment()
    node = os.environ.get("MEMORY_FLEET_NODE_ID", DEFAULT_NODE_ID)
    ap = argparse.ArgumentParser(prog="memory-fleet-register")
    ap.add_argument("--source", required=True, help="harness | ingest | mcp")
    ap.add_argument("--node", default=node)
    ap.add_argument("--heartbeat-s", type=int, default=DEFAULT_HEARTBEAT_S)
    ap.add_argument(
        "--activate",
        action="store_true",
        help="activate the validated draft (default: stop after validation)",
    )
    ap.add_argument("--dry-run", action="store_true", help="print the manifest and exit")
    args = ap.parse_args(argv)

    if args.node != node or args.source not in OWNED_SOURCES:
        raise SystemExit("Select the configured node and a supported service")

    collector = load_collector(args.source)
    man = manifest_mod.build(
        args.node, args.source, collector, expected_heartbeat_s=args.heartbeat_s
    )
    if "writes_total" in man["metrics"]:
        manifest_mod.declare_labels(man, "writes_total", ["route"])

    if args.dry_run:
        print(json.dumps(man, indent=2, sort_keys=True))
        return 0

    with HubClient() as hub:
        assert_design(hub)
        print(f"design ok: {DESIGN_REVISION} / protocol {PROTOCOL_VERSION}")

        try:
            existing = hub.call(
                "get_source", {"node": args.node, "source": args.source}
            )
            expected_revision = int(existing.get("revision", 0))
            print(f"existing source at revision {expected_revision}")
        except ToolError as e:
            if "not registered" not in str(e).lower():
                raise
            expected_revision = 0
            print("source not registered yet; expected_revision 0")

        propose_key = f"propose-{args.node}-{args.source}-r{expected_revision}"
        draft = hub.call(
            "propose_source",
            {
                "manifest": man,
                "expected_revision": expected_revision,
                "idempotency_key": propose_key,
            },
        )
        draft_id = draft.get("draft_id")
        print(f"draft {draft_id} (digest {draft.get('digest')})")

        samples = sample_events(args.node, args.source, collector)
        validation = hub.call(
            "validate_sample", {"draft_id": draft_id, "samples": samples}
        )
        digest = validation.get("validation_digest") or validation.get("digest")
        print(f"validated {len(samples)} live samples -> digest {digest}")
        if not digest:
            print(json.dumps(validation, indent=2), file=sys.stderr)
            raise SystemExit("no validation digest returned; not activating")

        if not args.activate:
            print("\nnot activating (pass --activate when this looks right)")
            return 0

        activated = hub.call(
            "activate_source",
            {
                "draft_id": draft_id,
                "validation_digest": digest,
                "expected_revision": expected_revision,
                "idempotency_key": f"activate-{draft_id}-{uuid.uuid4().hex[:8]}",
            },
        )
        print(f"activated: {json.dumps(activated)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
