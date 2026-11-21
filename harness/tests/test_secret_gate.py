import pytest

from memory_harness.db import meta
from memory_harness.db.connection import open_writer
from scripts.verify_secret_gate import _scan_feed_for_secrets, verify_secret_gate


@pytest.fixture
def owner_token(minter):  # type: ignore[no-untyped-def]
    token, _ = minter(
        principal_id="self", node="node-main", allowed_scopes=["shared"],
        trust_ceiling="user", home_scope="shared", link_authority="active",
    )
    return token


def test_planted_secrets_and_credential_absent_from_learning_feed(client, owner_token, settings) -> None:  # type: ignore[no-untyped-def]
    con = open_writer(settings.db.path)
    assert meta.get(con.cursor(), "secret_scan_verified") is None

    ok = verify_secret_gate(client, owner_token, con)
    assert ok is True
    assert meta.get(con.cursor(), "secret_scan_verified") == "1"
    con.close()

    feed = client.get("/v1/changes", params={"since": 0, "learning_eligible": True},
                      headers={"Authorization": f"Bearer {owner_token}"}).json()
    assert all(not i["redacted"] for i in feed["items"])
    assert all(
        not (i["record"] and i["record"].get("note_type") == "credential")
        for i in feed["items"]
    )


def test_leak_detector_catches_a_dirty_feed() -> None:
    import secrets
    synthetic_value = secrets.token_hex(12)
    leaks = _scan_feed_for_secrets(
        [{"content": f"the dump printed {synthetic_value} here"}],
        [synthetic_value, "unrelated"],
    )
    assert leaks == [synthetic_value]


def test_marker_not_set_when_verification_fails(client, owner_token, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import scripts.verify_secret_gate as vsg

    monkeypatch.setattr(vsg, "_scan_feed_for_secrets", lambda items, planted: planted[:1])
    con = open_writer(settings.db.path)
    ok = vsg.verify_secret_gate(client, owner_token, con)
    assert ok is False
    assert meta.get(con.cursor(), "secret_scan_verified") is None
    con.close()
