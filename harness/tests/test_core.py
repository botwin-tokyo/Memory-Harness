import pytest

from memory_harness.db.connection import open_reader
from memory_harness.db.writer import WriteQueue
from memory_harness.domain.core_memory import compose_core, edit_core
from memory_harness.models.api import CoreIn
from memory_harness.models.domain import Principal


@pytest.fixture
def owner_token(minter):  # type: ignore[no-untyped-def]
    token, _ = minter(
        principal_id="self", node="node-main", allowed_scopes=["shared"],
        trust_ceiling="user", home_scope="shared", link_authority="active",
    )
    return token


@pytest.fixture
def agent_token(minter):  # type: ignore[no-untyped-def]
    token, _ = minter(
        principal_id="agent-a", node="node-1", allowed_scopes=["project:atlas", "shared"],
        trust_ceiling="agent", home_scope="project:atlas",
    )
    return token


def post_core(client, token, **kw):  # type: ignore[no-untyped-def]
    body = {"op": "append", "slot_key": "goal", "value": "finish the atlas deploy"}
    body.update(kw)
    return client.post("/v1/core", json=body, headers={"Authorization": f"Bearer {token}"})


async def test_compose_shared_plus_own_private_only(wcon) -> None:  # type: ignore[no-untyped-def]
    wq = WriteQueue(wcon)
    self = Principal("k0", "self", "node-main", ["shared"], "user", "shared")
    agent_a = Principal("k1", "agent-a", "node-1", ["shared"], "agent", "shared")
    agent_b = Principal("k2", "agent-b", "node-2", ["shared"], "agent", "shared")
    await edit_core(wq, self, CoreIn(op="append", slot_key="identity", value="Assistant owns this fleet", owner_scope="shared"), shared_budget=800, agent_budget=800)
    await edit_core(wq, agent_a, CoreIn(op="append", slot_key="goal", value="agent-a goal"), shared_budget=800, agent_budget=800)
    await edit_core(wq, agent_b, CoreIn(op="append", slot_key="goal", value="agent-b secret goal"), shared_budget=800, agent_budget=800)

    slots = compose_core(wcon, agent_a, 2000, shared_budget=800, agent_budget=800)
    texts = [s["value_text"] for s in slots]
    assert "Assistant owns this fleet" in texts
    assert "agent-a goal" in texts
    assert "agent-b secret goal" not in texts
    wq.close()


async def test_compose_budget_drops_lowest_priority(wcon) -> None:  # type: ignore[no-untyped-def]
    wq = WriteQueue(wcon)
    agent_a = Principal("k1", "agent-a", "node-1", ["shared"], "agent", "shared")
    await edit_core(wq, agent_a, CoreIn(op="append", slot_key="goal", value="important pinned goal " * 40, priority=200, pinned=True), shared_budget=800, agent_budget=100)
    await edit_core(wq, agent_a, CoreIn(op="append", slot_key="note", value="trivial scratch note", priority=10), shared_budget=800, agent_budget=100)
    slots = compose_core(wcon, agent_a, 2000, shared_budget=800, agent_budget=100)
    texts = [s["value_text"] for s in slots]
    assert any("pinned goal" in t for t in texts)
    assert not any("trivial scratch" in t for t in texts)
    wq.close()


def test_agent_editing_shared_403(client, agent_token) -> None:  # type: ignore[no-untyped-def]
    r = post_core(client, agent_token, owner_scope="shared")
    assert r.status_code == 403


def test_agent_editing_foreign_private_403(client, agent_token) -> None:  # type: ignore[no-untyped-def]
    r = post_core(client, agent_token, owner_scope="private:agent-b")
    assert r.status_code == 403


def test_owner_edits_shared_ok(client, owner_token, settings) -> None:  # type: ignore[no-untyped-def]
    r = post_core(client, owner_token, owner_scope="shared", slot_key="identity")
    assert r.status_code == 200
    assert r.json()["slot"]["owner_scope"] == "shared"
    ev = open_reader(settings.db.path).execute(
        "SELECT event_type, target_type FROM events WHERE event_type='core_edited'"
    ).fetchone()
    assert ev is not None and ev[1] == "core"


async def test_overflow_demotes_lowest_unpinned_to_note(wcon) -> None:  # type: ignore[no-untyped-def]
    wq = WriteQueue(wcon)
    agent_a = Principal("k1", "agent-a", "node-1", ["shared"], "agent", "shared")
    budget = 20
    await edit_core(wq, agent_a, CoreIn(op="append", slot_key="pinned-goal", value="keep me " * 5, priority=300, pinned=True), shared_budget=800, agent_budget=budget)
    await edit_core(wq, agent_a, CoreIn(op="append", slot_key="mid", value="mid words " * 3, priority=100), shared_budget=800, agent_budget=budget)
    out = await edit_core(wq, agent_a, CoreIn(op="append", slot_key="new", value="new slot " * 3, priority=200), shared_budget=800, agent_budget=budget)

    assert out.demoted, "expected a demotion"
    remaining = wcon.execute("SELECT slot_key FROM core_memory WHERE owner_scope='private:agent-a'").fetchall()
    keys = {r[0] for r in remaining}
    assert "mid" not in keys
    assert "pinned-goal" in keys
    assert "new" in keys
    notes = wcon.execute("SELECT body FROM notes").fetchall()
    assert len(notes) == 1 and "mid words" in notes[0][0]
    types = [r[0] for r in wcon.execute("SELECT event_type FROM events ORDER BY seq")]
    assert "core_edited" in types and "note_created" in types
    wq.close()


def test_replace_and_remove(client, agent_token) -> None:  # type: ignore[no-untyped-def]
    post_core(client, agent_token, slot_key="goal", value="v1")
    r = post_core(client, agent_token, op="replace", slot_key="goal", value="v2", pinned=True)
    assert r.status_code == 200 and r.json()["slot"]["value_text"] == "v2"
    assert r.json()["slot"]["pinned"] is True
    r = post_core(client, agent_token, op="remove", slot_key="goal")
    assert r.status_code == 200 and r.json()["slot"] is None
    r = post_core(client, agent_token, op="remove", slot_key="goal")
    assert r.status_code == 404


def test_value_required_for_append(client, agent_token) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/v1/core",
        json={"op": "append", "slot_key": "goal"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert r.status_code == 400
