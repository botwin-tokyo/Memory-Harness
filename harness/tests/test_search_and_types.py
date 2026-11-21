from __future__ import annotations

import json

import pytest

from memory_contract import EMAIL_BACKUP_TAG, EMAIL_MESSAGE_TAG, EMAIL_TAG, NO_EMBED_TAG

BASE = [EMAIL_TAG, "mailbox:user@example.test"]


@pytest.fixture(autouse=True)
def semantic_backend(monkeypatch, embeddings_client):
    import memory_harness.main as main
    monkeypatch.setattr(main, "EmbeddingsClient", lambda *a, **kw: embeddings_client)


def _token(minter):  # type: ignore[no-untyped-def]
    token, _ = minter(principal_id="self", node="node-ingest",
                      allowed_scopes=["shared", "reference", "library"],
                      link_authority="active")
    return token


def _put(client, token, *, lid, body, tags, type_="email"):  # type: ignore[no-untyped-def]
    return client.post("/v1/intake", json={
        "type": type_, "memory_id": lid, "body": body, "emotion": "Neutrality",
        "subject_class": "world", "scope": "reference",
        "source_class": "tool_output", "learning_eligible": False, "tags": tags,
    }, headers={"Authorization": f"Bearer {token}"})


def _search(client, token, **kw):  # type: ignore[no-untyped-def]
    from memory_harness.db.connection import open_writer
    from memory_harness.workers.embed_worker import drain_embed_jobs
    from scripts.bootstrap_vectors import bootstrap_vectors
    state = client.app.state
    con = open_writer(state.settings.db.path)
    try:
        bootstrap_vectors(con, state.embeddings)
    finally:
        con.close()
    client.portal.call(drain_embed_jobs, state.writer, state.content_store, state.embeddings)
    return client.post("/v1/search", json=kw,
                       headers={"Authorization": f"Bearer {token}"})


def test_types_lists_email(client, minter) -> None:  # type: ignore[no-untyped-def]
    "The app's green light. Until this says `email`, it writes nothing."
    r = client.get("/v1/types",
                   headers={"Authorization": f"Bearer {_token(minter)}"})
    assert r.status_code == 200
    assert "email" in r.json()["types"]


def test_types_cannot_claim_support_the_write_path_lacks(client, minter) -> None:  # type: ignore[no-untyped-def]
    from memory_harness.models.domain import MEMORY_TYPES

    r = client.get("/v1/types",
                   headers={"Authorization": f"Bearer {_token(minter)}"})
    assert r.json()["types"] == list(MEMORY_TYPES)


def test_types_needs_a_key(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/v1/types").status_code in (401, 403)


def test_search_finds_an_email_by_its_text(client, minter) -> None:  # type: ignore[no-untyped-def]
    'Intake through worker embedding and semantic API retrieval.'
    token = _token(minter)
    _put(client, token, lid="m-1",
         body=json.dumps({"subject": "Workshop update",
                          "text": "the session now starts at 14:00"}),
         tags=[*BASE, EMAIL_MESSAGE_TAG, "folder:inbox"])
    r = _search(client, token, query="Workshop update",
                types=["email"], tags_all=BASE, limit=10)
    assert r.status_code == 200, r.text
    assert [h["lineage_id"] for h in r.json()["results"]] == ["m-1"]


def test_a_raw_backup_never_surfaces(client, minter) -> None:  # type: ignore[no-untyped-def]
    token = _token(minter)
    _put(client, token, lid="raw-1",
         body=json.dumps({"raw": "V29ya3Nob3AgdXBkYXRl" * 20}),
         tags=[*BASE, EMAIL_BACKUP_TAG, NO_EMBED_TAG])
    r = _search(client, token, query="workshop", types=["email"], limit=50)
    assert "raw-1" not in [h["lineage_id"] for h in r.json()["results"]]


def test_types_filter_separates_email_from_ordinary_notes(client, minter) -> None:  # type: ignore[no-untyped-def]
    token = _token(minter)
    _put(client, token, lid="m-2", body=json.dumps({"text": "quarterly report"}),
         tags=[*BASE, EMAIL_MESSAGE_TAG])
    client.post("/v1/intake", json={
        "type": "note", "memory_id": "n-2", "body": "quarterly report",
        "emotion": "Neutrality", "subject_class": "world", "scope": "reference",
        "tags": ["ordinary"],
    }, headers={"Authorization": f"Bearer {token}"})

    got = _search(client, token, query="quarterly report",
                  types=["email"], limit=50).json()["results"]
    assert [h["lineage_id"] for h in got] == ["m-2"]
    assert all(h["type"] == "email" for h in got)


def test_tags_all_requires_every_tag(client, minter) -> None:  # type: ignore[no-untyped-def]
    token = _token(minter)
    _put(client, token, lid="m-3", body=json.dumps({"text": "shared subject"}),
         tags=[*BASE, EMAIL_MESSAGE_TAG])
    _put(client, token, lid="m-4", body=json.dumps({"text": "shared subject"}),
         tags=[EMAIL_TAG, "mailbox:someone@else.test", EMAIL_MESSAGE_TAG])
    got = _search(client, token, query="shared subject",
                  tags_all=BASE, limit=50).json()["results"]
    assert [h["lineage_id"] for h in got] == ["m-3"]


def test_tags_any_needs_only_one(client, minter) -> None:  # type: ignore[no-untyped-def]
    token = _token(minter)
    _put(client, token, lid="m-5", body=json.dumps({"text": "folder test"}),
         tags=[*BASE, EMAIL_MESSAGE_TAG, "folder:archive"])
    got = _search(client, token, query="folder test",
                  tags_any=["folder:inbox", "folder:archive"],
                  limit=50).json()["results"]
    assert "m-5" in [h["lineage_id"] for h in got]


def test_a_retracted_email_never_surfaces(client, minter) -> None:  # type: ignore[no-untyped-def]
    'A hit on a retracted node is a hit on mail the user deleted.'
    token = _token(minter)
    _put(client, token, lid="m-6", body=json.dumps({"text": "delete me please"}),
         tags=[*BASE, EMAIL_MESSAGE_TAG])
    client.post("/v1/forget", json={"id": "m-6", "reason": "user deleted"},
                headers={"Authorization": f"Bearer {token}"})
    got = _search(client, token, query="delete me please", limit=50).json()["results"]
    assert "m-6" not in [h["lineage_id"] for h in got]


def test_only_the_head_revision_surfaces(client, minter) -> None:  # type: ignore[no-untyped-def]
    'Moving mail writes a revision; the old one must not also match.'
    token = _token(minter)
    _put(client, token, lid="m-7", body=json.dumps({"text": "version one"}),
         tags=[*BASE, EMAIL_MESSAGE_TAG, "folder:inbox"])
    _put(client, token, lid="m-7", body=json.dumps({"text": "version two"}),
         tags=[*BASE, EMAIL_MESSAGE_TAG, "folder:archive"])
    got = _search(client, token, query="version", limit=50).json()["results"]
    hits = [h for h in got if h["lineage_id"] == "m-7"]
    assert len(hits) == 1
    assert "version two" in hits[0]["text_excerpt"]


def test_an_unknown_type_returns_nothing_rather_than_everything(client, minter) -> None:  # type: ignore[no-untyped-def]
    token = _token(minter)
    _put(client, token, lid="m-8", body=json.dumps({"text": "anything"}),
         tags=[*BASE, EMAIL_MESSAGE_TAG])
    r = _search(client, token, query="anything", types=["nonsense"], limit=50)
    assert r.json()["results"] == []


def test_limit_is_clamped_not_rejected(client, minter) -> None:  # type: ignore[no-untyped-def]
    token = _token(minter)
    _put(client, token, lid="m-9", body=json.dumps({"text": "clamp me"}),
         tags=[*BASE, EMAIL_MESSAGE_TAG])
    assert _search(client, token, query="clamp me", limit=5000).status_code == 200


def test_search_needs_a_key(client) -> None:  # type: ignore[no-untyped-def]
    assert client.post("/v1/search", json={"query": "x"}).status_code in (401, 403)


def test_scope_is_enforced_exactly_as_it_is_everywhere_else(client, minter) -> None:  # type: ignore[no-untyped-def]
    'Search reuses `visible_scopes`; it does not invent its own rule.'
    token = _token(minter)
    _put(client, token, lid="m-10", body=json.dumps({"text": "reference matter"}),
         tags=[*BASE, EMAIL_MESSAGE_TAG])
    narrow, _ = minter(principal_id="narrow", node="n", allowed_scopes=["shared"],
                       home_scope="shared")

    got = _search(client, narrow, query="reference matter", limit=50).json()["results"]
    assert "m-10" in [h["lineage_id"] for h in got], "reference is universal"

    client.post("/v1/intake", json={
        "type": "note", "memory_id": "p-1", "body": "someone elses secret",
        "emotion": "Neutrality", "subject_class": "world",
        "scope": "private:self", "tags": ["x"],
    }, headers={"Authorization": f"Bearer {token}"})
    got = _search(client, narrow, query="someone elses secret", limit=50).json()["results"]
    assert "p-1" not in [h["lineage_id"] for h in got], "another principal's private"


def test_search_reports_embedding_outage(client, minter, fake_bge):
    fake_bge.status = "loading"
    response = client.post("/v1/search", json={"query": "workshop"},
                           headers={"Authorization": f"Bearer {_token(minter)}"})
    assert response.status_code == 503


def test_search_reports_missing_vector_index(client, minter):
    response = client.post("/v1/search", json={"query": "workshop"},
                           headers={"Authorization": f"Bearer {_token(minter)}"})
    assert response.status_code == 503
    assert "index not initialized" in response.json()["detail"]
