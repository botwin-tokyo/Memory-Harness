import pytest

from memory_harness.db.connection import open_reader

SYNTHETIC_KEY = ("ab" * 32)


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


def post(client, token, **kw):  # type: ignore[no-untyped-def]
    body = {"type": "fact", "body": "the atlas db runs postgres 16", "emotion": "Satisfaction"}
    body.update(kw)
    return client.post("/v1/remember", json=body, headers={"Authorization": f"Bearer {token}"})


def rows(settings, sql, params=()):  # type: ignore[no-untyped-def]
    con = open_reader(settings.db.path)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def test_missing_emotion_400(client, agent_token) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/v1/remember",
        json={"type": "fact", "body": "no emotion here"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert r.status_code == 400


def test_invalid_emotion_400(client, agent_token) -> None:  # type: ignore[no-untyped-def]
    assert post(client, agent_token, emotion="meh?").status_code == 400


def test_fact_write_returns_ref_and_stores_emotion(client, agent_token, settings) -> None:  # type: ignore[no-untyped-def]
    r = post(client, agent_token, emotion="Curiosity")
    assert r.status_code == 200
    body = r.json()
    assert body["ref"] == f"{body['lineage_id']}.1"
    assert body["revision"] == 1
    assert body["superseded"] is None
    row = rows(settings, "SELECT emotion_id, gloss, source_trust FROM facts")[0]
    assert row[0] == 37
    assert "postgres 16" in row[1]
    assert row[2] == "agent"


def test_user_head_stays_agent_disputed(client, owner_token, agent_token, settings) -> None:  # type: ignore[no-untyped-def]
    r1 = post(client, owner_token)
    lid = r1.json()["lineage_id"]
    r2 = post(client, agent_token, memory_id=lid, body="no, it runs sqlite", scope="shared")
    assert r2.status_code == 200
    heads = rows(settings, "SELECT status, is_head, source_trust FROM facts WHERE is_head=1")
    assert len(heads) == 1 and heads[0][2] == "user" and heads[0][0] == "active"
    disputed = rows(settings, "SELECT status, source_trust FROM facts WHERE status='disputed'")
    assert len(disputed) == 1 and disputed[0][1] == "agent"


def test_trusted_tool_revise_supersedes(client, owner_token, minter, settings) -> None:  # type: ignore[no-untyped-def]
    r1 = post(client, owner_token)
    lid = r1.json()["lineage_id"]
    tt, _ = minter(
        principal_id="consolidator", node="test-pipeline", allowed_scopes=["shared"],
        trust_ceiling="trusted_tool", home_scope="shared",
    )
    r2 = post(client, tt, memory_id=lid, body="consolidation disputes this")
    assert rows(settings, "SELECT COUNT(*) FROM facts WHERE status='disputed'")[0][0] == 1
    r3 = post(client, owner_token, memory_id=lid, body="the atlas db runs postgres 17")
    assert r3.json()["ref"] == f"{lid}.2"
    assert r3.json()["superseded"] is not None
    rows_ = rows(settings, "SELECT revision, status, is_head FROM facts WHERE status!='disputed' ORDER BY revision")
    assert [(r[0], r[1], r[2]) for r in rows_] == [(1, "superseded", 0), (2, "active", 1)]


def test_unknown_type_400(client, agent_token) -> None:  # type: ignore[no-untyped-def]
    assert post(client, agent_token, type="manifesto").status_code == 400


def test_preference_upsert_bumps_evidence(client, agent_token, settings) -> None:  # type: ignore[no-untyped-def]
    'Saying the SAME thing again is evidence, not news (0010).'
    post(client, agent_token, type="preference", body="prefers dark themes")
    post(client, agent_token, type="preference", body="prefers dark themes")
    heads = rows(settings, "SELECT evidence_count, statement, revision FROM"
                           " preferences WHERE is_head=1 AND active=1")
    assert len(heads) == 1
    assert heads[0][0] == 2
    assert heads[0][2] == 1
    lineages = rows(settings, "SELECT DISTINCT lineage_id FROM preferences")
    assert len(lineages) == 1
    assert rows(settings, "SELECT COUNT(*) FROM preferences")[0][0] == 1


def test_a_different_phrasing_still_revises_and_is_kept(client, agent_token, settings) -> None:  # type: ignore[no-untyped-def]
    'The other half: variation is preserved rather than collapsed.'
    post(client, agent_token, type="preference", body="prefers dark themes",
         category="ui", item="theme")
    post(client, agent_token, type="preference", item="theme", category="ui",
         body="finds bright interfaces physically tiring after dusk")
    heads = rows(settings, "SELECT statement, revision, facets FROM preferences"
                           " WHERE is_head=1 AND active=1")
    assert len(heads) == 1
    assert heads[0][0] == "prefers dark themes"
    assert heads[0][1] == 2
    assert "physically tiring" in heads[0][2]


def test_credential_stored_raw_never_trained(client, agent_token, settings) -> None:  # type: ignore[no-untyped-def]
    r = post(client, agent_token, type="credential", body=f"wifi password is {SYNTHETIC_KEY}")
    assert r.status_code == 200
    row = rows(settings, "SELECT note_type, body, learning_eligible FROM notes")[0]
    assert row[0] == "credential"
    assert SYNTHETIC_KEY in row[1]
    assert row[2] == 0


def test_secret_in_fact_body_redacted(client, agent_token, settings) -> None:  # type: ignore[no-untyped-def]
    post(client, agent_token, body=f"the key is {SYNTHETIC_KEY} oops")
    row = rows(settings, "SELECT gloss, learning_eligible FROM facts")[0]
    assert SYNTHETIC_KEY not in row[0] and "[REDACTED-SECRET]" in row[0]
    assert row[1] == 0
