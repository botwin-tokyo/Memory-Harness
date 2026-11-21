"The pin against the hub's contract."

from __future__ import annotations

from memory_fleet import design


def test_log_is_not_a_publishable_kind() -> None:
    assert "log" not in design.PUBLISHED_KINDS


def test_published_kinds_all_have_a_payload_schema() -> None:
    for kind in design.PUBLISHED_KINDS:
        assert kind in design.PAYLOAD_SCHEMAS


def test_manifest_key_matches_the_live_hub_self_registration_shape() -> None:
    assert (design.manifest_schema_key("node-a", "source-a", "health")
            == "node-a/source-a.health@1.0.0")


def test_event_schema_id_is_unversioned_and_differs_from_the_manifest_key() -> None:
    "The manifest key and the event's schema_id are two different strings."
    assert design.schema_id("node-a", "source-a", "health") == "node-a/source-a.health"
    assert design.schema_id("node-a", "source-a", "health") != design.manifest_schema_key(
        "node-a", "source-a", "health"
    )


def test_limits_match_what_get_design_returned() -> None:
    assert design.EVENT_BYTES_MAX == 65_536
    assert design.FRAME_BYTES_MAX == 262_144
    assert design.BATCH_EVENTS_MAX == 100
