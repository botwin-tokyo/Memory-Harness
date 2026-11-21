import sqlite3

import pytest

from memory_harness.db.connection import open_reader
from memory_harness.workers import queue

KEY = ("ab" * 32)


@pytest.fixture
def agent_token(minter):  # type: ignore[no-untyped-def]
    token, _ = minter(
        principal_id="agent-a",
        node="node-1",
        allowed_scopes=["project:atlas", "shared"],
        trust_ceiling="agent",
        home_scope="project:atlas",
    )
    return token


def post(client, token, **kw):  # type: ignore[no-untyped-def]
    body = {"event_type": "user_msg", "idempotency_key": "k-1", "content": "hello"}
    body.update(kw)
    return client.post("/v1/events", json=body, headers={"Authorization": f"Bearer {token}"})


def rows(settings, sql, params=()):  # type: ignore[no-untyped-def]
    con = open_reader(settings.db.path)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def test_same_idempotency_key_dedupes(client, agent_token, settings) -> None:  # type: ignore[no-untyped-def]
    r1 = post(client, agent_token)
    assert r1.status_code == 200
    r2 = post(client, agent_token)
    assert r2.status_code == 200
    assert r1.json()["event_id"] == r2.json()["event_id"]
    assert r2.json()["deduped"] is True
    assert r1.json()["deduped"] is False
    assert len(rows(settings, "SELECT * FROM events")) == 1


def test_identical_content_different_key_appends(client, agent_token, settings) -> None:  # type: ignore[no-untyped-def]
    r1 = post(client, agent_token, idempotency_key="k-a")
    r2 = post(client, agent_token, idempotency_key="k-b")
    assert r1.json()["event_id"] != r2.json()["event_id"]
    assert r2.json()["deduped"] is False
    assert len(rows(settings, "SELECT * FROM events")) == 2


def test_over_cap_body_goes_to_blob(client, agent_token, settings) -> None:  # type: ignore[no-untyped-def]
    big = "x" * (settings.db.inline_body_max_bytes + 10)
    r = post(client, agent_token, content=big)
    assert r.status_code == 200
    row = rows(settings, "SELECT content, content_ref FROM events")[0]
    assert row[0] is None
    assert row[1] is not None and len(row[1]) == 64


def test_secret_redacted_and_not_learning_eligible(client, agent_token, settings) -> None:  # type: ignore[no-untyped-def]
    r = post(client, agent_token, content=f"tool dumped X-API-Key: {KEY} into the result")
    assert r.status_code == 200
    assert r.json()["redacted"] is True
    row = rows(settings, "SELECT content, learning_eligible, redacted FROM events")[0]
    assert KEY not in row[0] and "[REDACTED-SECRET]" in row[0]
    assert row[1] == 0
    assert row[2] == 1


def test_no_emotion_defaults_127(client, agent_token, settings) -> None:  # type: ignore[no-untyped-def]
    post(client, agent_token)
    assert rows(settings, "SELECT emotion_id FROM events")[0][0] == 127


def test_scope_defaults_to_home(client, agent_token, settings) -> None:  # type: ignore[no-untyped-def]
    post(client, agent_token)
    assert rows(settings, "SELECT scope FROM events")[0][0] == "project:atlas"


def test_forbidden_scope_403(client, agent_token) -> None:  # type: ignore[no-untyped-def]
    r = post(client, agent_token, scope="private:someone-else")
    assert r.status_code == 403


def test_source_trust_from_key_and_origin(client, agent_token, settings) -> None:  # type: ignore[no-untyped-def]
    post(client, agent_token, idempotency_key="t1")
    post(client, agent_token, idempotency_key="t2", source_class="external")
    trust = [r[0] for r in rows(settings, "SELECT source_trust FROM events ORDER BY seq")]
    assert trust == ["agent", "untrusted"]


def test_emotion_resolved_and_unknown_400(client, agent_token, settings) -> None:  # type: ignore[no-untyped-def]
    post(client, agent_token, idempotency_key="e1", emotion="Curiosity")
    assert rows(settings, "SELECT emotion_id FROM events")[0][0] == 37
    assert post(client, agent_token, idempotency_key="e2", emotion="meh?").status_code == 400


def test_unknown_event_type_400(client, agent_token) -> None:  # type: ignore[no-untyped-def]
    assert post(client, agent_token, event_type="fact_minted").status_code == 400


def test_jobs_enqueued(client, agent_token, settings) -> None:  # type: ignore[no-untyped-def]
    post(client, agent_token, idempotency_key="j1")
    post(client, agent_token, idempotency_key="j2", salience_hint="high")
    kinds = [
        (r[0], r[1])
        for r in rows(settings, "SELECT kind, state FROM jobs ORDER BY created_at")
    ]
    assert kinds == [("embed", "pending"), ("embed", "pending"), ("extract", "pending")]


def test_queue_claim_complete(wcon) -> None:  # type: ignore[no-untyped-def]
    cur = wcon.cursor()
    queue.enqueue(cur, "embed", {"ref_id": "e1"})
    queue.enqueue(cur, "embed", {"ref_id": "e2"})
    claimed = queue.claim(cur, "embed", 10)
    assert len(claimed) == 2 and all(r["state"] == "running" for r in claimed)
    assert queue.claim(cur, "embed", 10) == []
    queue.complete(cur, claimed[0]["job_id"])
    row = cur.execute("SELECT state FROM jobs WHERE job_id=?", (claimed[0]["job_id"],)).fetchone()
    assert row[0] == "done"
    assert queue.queue_depth(cur, "embed") == 0


def test_queue_fail_backoff_and_max_attempts(wcon) -> None:  # type: ignore[no-untyped-def]
    cur = wcon.cursor()
    jid = queue.enqueue(cur, "extract", {"ref_id": "e9"})
    for attempt in range(1, queue.MAX_ATTEMPTS + 1):
        claimed = queue.claim(cur, "extract", 1)
        assert len(claimed) == 1 and claimed[0]["attempts"] == attempt
        queue.fail(cur, jid, f"boom {attempt}")
        row = cur.execute("SELECT state, run_after FROM jobs WHERE job_id=?", (jid,)).fetchone()
        if attempt < queue.MAX_ATTEMPTS:
            assert row[0] == "pending"
            assert row[1] > 0
            cur.execute("UPDATE jobs SET run_after=0 WHERE job_id=?", (jid,))
        else:
            assert row[0] == "failed"
