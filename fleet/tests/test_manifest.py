'The manifest is derived from the collector so the two cannot drift.'

from __future__ import annotations

import pytest

from memory_fleet import manifest as manifest_mod
from memory_fleet.design import PUBLISHED_KINDS, manifest_schema_key, schema_id


def test_manifest_declares_a_schema_for_every_published_kind(collector) -> None:
    man = manifest_mod.build("memory-node", "harness", collector, expected_heartbeat_s=30)
    for kind in PUBLISHED_KINDS:
        assert manifest_schema_key("memory-node", "harness", kind) in man["schemas"]


def test_manifest_keys_are_versioned_but_event_ids_are_not(collector) -> None:
    man = manifest_mod.build("memory-node", "harness", collector, expected_heartbeat_s=30)
    for kind in PUBLISHED_KINDS:
        assert schema_id("memory-node", "harness", kind) not in man["schemas"]


def test_manifest_never_declares_a_log_schema(collector) -> None:
    man = manifest_mod.build("memory-node", "harness", collector, expected_heartbeat_s=30)
    assert not any(".log@" in key for key in man["schemas"])


def test_every_collector_metric_is_declared(collector) -> None:
    man = manifest_mod.build("memory-node", "harness", collector, expected_heartbeat_s=30)
    emitted = {s.name for s in collector.metrics()}
    assert emitted <= set(man["metrics"])


def test_checks_come_from_the_collector(collector) -> None:
    man = manifest_mod.build("memory-node", "harness", collector, expected_heartbeat_s=30)
    assert man["checks"] == list(collector.check_names)


def test_push_transport_needs_no_operator_endpoint_approval(collector) -> None:
    man = manifest_mod.build("memory-node", "harness", collector, expected_heartbeat_s=30)
    assert man["transport"] == "push"
    assert man["endpoint_ref"] is None
    assert man["subscribe"] is None


def test_we_promise_replay_because_we_spool(collector) -> None:
    man = manifest_mod.build("memory-node", "harness", collector, expected_heartbeat_s=30)
    assert man["delivery_capability"] == "replay"


def test_rejects_names_the_hub_would_reject(collector) -> None:
    collector.check_names = ("not a valid name",)
    with pytest.raises(ValueError, match="does not match"):
        manifest_mod.build("memory-node", "harness", collector, expected_heartbeat_s=30)


def test_label_cap_is_enforced(collector) -> None:
    man = manifest_mod.build("memory-node", "harness", collector, expected_heartbeat_s=30)
    with pytest.raises(ValueError, match="exceeds the cap"):
        manifest_mod.declare_labels(man, "widgets", ["a", "b", "c", "d", "e", "f"])
