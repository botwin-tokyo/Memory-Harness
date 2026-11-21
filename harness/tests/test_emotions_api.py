import pytest

from memory_harness.domain.emotions import UnknownEmotion, resolve_emotion


def test_emotions_route_returns_127(client, minter) -> None:  # type: ignore[no-untyped-def]
    token, _ = minter(
        principal_id="agent-a", node="node-1", allowed_scopes=["project:atlas"]
    )
    r = client.get("/v1/emotions", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert "max-age" in r.headers.get("cache-control", "")
    rows = r.json()
    assert len(rows) == 127
    assert rows[0]["emotion_id"] == 1
    assert rows[-1] == {
        "emotion_id": 127,
        "name": "Neutrality",
        "family": "neutral",
        "description": rows[-1]["description"],
    }
    assert all(set(r_) == {"emotion_id", "name", "family", "description"} for r_ in rows)


def test_resolve_by_name(wcon) -> None:  # type: ignore[no-untyped-def]
    assert resolve_emotion(wcon, "Grief") == 113
    assert resolve_emotion(wcon, "grief") == 113
    assert resolve_emotion(wcon, "Neutrality") == 127


def test_resolve_by_id(wcon) -> None:  # type: ignore[no-untyped-def]
    assert resolve_emotion(wcon, "113") == 113
    assert resolve_emotion(wcon, 113) == 113


def test_resolve_unknown_raises(wcon) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(UnknownEmotion):
        resolve_emotion(wcon, "elated?")
    with pytest.raises(UnknownEmotion):
        resolve_emotion(wcon, "999")
    with pytest.raises(UnknownEmotion):
        resolve_emotion(wcon, "happy-ish")
