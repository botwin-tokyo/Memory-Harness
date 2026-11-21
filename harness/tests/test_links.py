import pytest

from memory_harness.db.connection import open_reader


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


def remember(client, token, **kw):  # type: ignore[no-untyped-def]
    body = {"type": "fact", "body": "placeholder", "emotion": "Neutrality", "scope": "shared"}
    body.update(kw)
    r = client.post("/v1/remember", json=body, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return r.json()


def two_lineages(client, token):  # type: ignore[no-untyped-def]
    a = remember(client, token, body="self likes nine inch nails")
    b = remember(client, token, body="nine inch nails plays synths")
    return a["lineage_id"], b["lineage_id"]


def link(client, token, src, dst, **kw):  # type: ignore[no-untyped-def]
    body = {"src_lineage_id": src, "dst_lineage_id": dst, "link_type": "relates_to"}
    body.update(kw)
    return client.post("/v1/link", json=body, headers={"Authorization": f"Bearer {token}"})


def test_propose_authority_lands_proposed(client, agent_token) -> None:  # type: ignore[no-untyped-def]
    src, dst = two_lineages(client, agent_token)
    r = link(client, agent_token, src, dst)
    assert r.status_code == 200
    assert r.json()["status"] == "proposed"
    con = open_reader(client.app.state.settings.db.path)
    row = con.execute("SELECT status, proposed_by, curated_by FROM memory_links").fetchone()
    assert row[0] == "proposed"
    assert row[1] == "agent-a"
    assert row[2] is None
    con.close()


def test_active_authority_lands_active(client, owner_token) -> None:  # type: ignore[no-untyped-def]
    src, dst = two_lineages(client, owner_token)
    r = link(client, owner_token, src, dst)
    assert r.json()["status"] == "active"
    con = open_reader(client.app.state.settings.db.path)
    row = con.execute("SELECT status, curated_by FROM memory_links").fetchone()
    assert row[0] == "active" and row[1] == "self"
    con.close()


def test_link_to_invisible_target_refused(client, agent_token, minter) -> None:  # type: ignore[no-untyped-def]
    t2, _ = minter(principal_id="agent-b", node="node-2", allowed_scopes=[],
                   home_scope="private:agent-b")
    src = remember(client, agent_token, body="agent-a shared thing")["lineage_id"]
    dst = remember(client, t2, body="agent-b private thing", scope="private:agent-b")["lineage_id"]
    r = link(client, agent_token, src, dst)
    assert r.status_code == 403


def test_link_to_missing_lineage_404(client, agent_token) -> None:  # type: ignore[no-untyped-def]
    src = remember(client, agent_token, body="a real thing")["lineage_id"]
    r = link(client, agent_token, src, "no-such-lineage")
    assert r.status_code == 404


def test_link_mutation_event(client, agent_token) -> None:  # type: ignore[no-untyped-def]
    src, dst = two_lineages(client, agent_token)
    r = link(client, agent_token, src, dst)
    con = open_reader(client.app.state.settings.db.path)
    ev = con.execute(
        "SELECT event_type, target_type, target_id, content FROM events"
        " WHERE event_type='link_proposed'"
    ).fetchone()
    assert ev is not None and ev[1] == "link" and ev[2] == r.json()["link_id"]
    assert "relates_to" in ev[3]
    con.close()


def test_free_form_and_bad_link_types(client, agent_token) -> None:  # type: ignore[no-untyped-def]
    src, dst = two_lineages(client, agent_token)
    assert link(client, agent_token, src, dst, link_type="reminds_me_of").status_code == 200
    assert link(client, agent_token, src, dst, link_type="").status_code == 400
