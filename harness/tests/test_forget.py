import pytest

from memory_harness.db.connection import open_reader


@pytest.fixture
def owner_token(minter):  # type: ignore[no-untyped-def]
    token, _ = minter(
        principal_id="self", node="node-main", allowed_scopes=["shared"],
        trust_ceiling="user", home_scope="shared", link_authority="active",
    )
    return token


def remember(client, token, **kw):  # type: ignore[no-untyped-def]
    body = {"type": "fact", "body": "placeholder", "emotion": "Neutrality", "scope": "shared"}
    body.update(kw)
    return client.post("/v1/remember", json=body, headers={"Authorization": f"Bearer {token}"})


def test_forget_removes_from_recall_fts_core_emits_tombstone(client, owner_token, settings) -> None:  # type: ignore[no-untyped-def]
    r1 = remember(client, owner_token, body="the zqxwv telescope lives on the roof")
    lid = r1.json()["lineage_id"]
    rid = r1.json()["ref"].split(".")[0]
    con = open_reader(settings.db.path)
    row_id = con.execute("SELECT fact_id FROM facts WHERE lineage_id=?", (lid,)).fetchone()[0]
    con.close()

    pack = client.post("/v1/recall", json={"query": "zqxwv telescope"},
                       headers={"Authorization": f"Bearer {owner_token}"}).json()
    assert any("zqxwv" in f["text"] for f in pack["facts"])

    out = client.post("/v1/forget",
                      json={"selector": {"type": "fact", "lineage_id": lid}, "reason": "wrong location"},
                      headers={"Authorization": f"Bearer {owner_token}"})
    assert out.status_code == 200 and out.json()["retracted"] >= 1

    con = open_reader(settings.db.path)
    row = con.execute("SELECT status, is_head FROM facts WHERE fact_id=?", (row_id,)).fetchone()
    assert (row[0], row[1]) == ("retracted", 0)
    assert con.execute("SELECT COUNT(*) FROM fts_memories WHERE ref_id=?", (row_id,)).fetchone()[0] == 0
    tomb = con.execute("SELECT target_type, target_id FROM events WHERE event_type='forget_executed'").fetchone()
    assert (tomb[0], tomb[1]) == ("fact", row_id)
    assert con.execute("SELECT COUNT(*) FROM events WHERE event_type='fact_minted'").fetchone()[0] == 1
    con.close()

    pack2 = client.post("/v1/recall", json={"query": "zqxwv telescope"},
                        headers={"Authorization": f"Bearer {owner_token}"}).json()
    assert not any("zqxwv" in f["text"] for f in pack2["facts"])
    feed = client.get("/v1/changes", params={"since": 0},
                      headers={"Authorization": f"Bearer {owner_token}"}).json()
    assert any(t["target_id"] == row_id for t in feed["tombstones"])


def test_forget_cascades_to_promoted_core_slot(client, owner_token, settings) -> None:  # type: ignore[no-untyped-def]
    r1 = remember(client, owner_token, body="zqxwv promoted fact")
    lid = r1.json()["lineage_id"]
    con_r = open_reader(settings.db.path)
    row_id = con_r.execute("SELECT fact_id FROM facts WHERE lineage_id=?", (lid,)).fetchone()[0]
    con_r.close()
    from memory_harness.db.connection import open_writer
    w = open_writer(settings.db.path)
    w.execute(
        "INSERT INTO core_memory(slot_id, slot_key, owner_scope, value_text,"
        " priority, token_estimate, promoted_from, created_by_agent, updated_at)"
        " VALUES ('slot-x', 'note', 'shared', 'zqxwv promoted', 100, 2, ?, 'self', 1)",
        (row_id,),
    )
    w.close()

    client.post("/v1/forget", json={"id": row_id, "reason": "cleanup"},
                headers={"Authorization": f"Bearer {owner_token}"})
    con = open_reader(settings.db.path)
    assert con.execute("SELECT COUNT(*) FROM core_memory WHERE promoted_from=?", (row_id,)).fetchone()[0] == 0
    con.close()


def test_forget_requires_id_or_selector(client, owner_token) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/v1/forget", json={"reason": "no target"},
                    headers={"Authorization": f"Bearer {owner_token}"})
    assert r.status_code == 400


def test_cannot_forget_invisible_scope(client, owner_token, minter) -> None:  # type: ignore[no-untyped-def]
    t2, _ = minter(principal_id="agent-b", node="node-2", allowed_scopes=[], home_scope="private:agent-b")
    r1 = client.post("/v1/remember", json={"type": "fact", "body": "agent-b private zqxwv",
                                           "emotion": "Neutrality", "scope": "private:agent-b"},
                     headers={"Authorization": f"Bearer {t2}"})
    lid = r1.json()["lineage_id"]
    out = client.post("/v1/forget",
                      json={"selector": {"type": "fact", "lineage_id": lid}, "reason": "overreach"},
                      headers={"Authorization": f"Bearer {owner_token}"})
    assert out.json()["retracted"] == 0


def test_forget_purges_the_library_vector_too(client, minter, settings) -> None:  # type: ignore[no-untyped-def]
    "A book chunk's embedding lives in `vec_library`, not `vec`."
    token, _ = minter(
        principal_id="self", node="node-main",
        allowed_scopes=["shared", "library"], trust_ceiling="user",
        home_scope="shared", link_authority="active",
    )
    r = client.post("/v1/remember", headers={"Authorization": f"Bearer {token}"},
                    json={"type": "note", "body": "the spice must flow",
                          "emotion": "Neutrality", "scope": "library",
                          "tags": ["book-chunk", "book:sample", "page:12"]})
    assert r.status_code == 200, r.text
    lid = r.json()["lineage_id"]

    con = open_reader(settings.db.path)
    row_id = con.execute("SELECT note_id FROM notes WHERE lineage_id=?",
                         (lid,)).fetchone()[0]
    con.close()

    from memory_harness.db.connection import open_writer

    w = open_writer(settings.db.path)
    try:
        w.execute("CREATE VIRTUAL TABLE IF NOT EXISTS vec_library USING vec0("
                  "embedding float[8], book text, ref_id text)")
        w.execute("INSERT INTO vec_library(embedding, book, ref_id) VALUES (?,?,?)",
                  (b"\x00" * (4 * 8), "sample", row_id))
        w.commit()
        assert w.execute("SELECT COUNT(*) FROM vec_library WHERE ref_id=?",
                         (row_id,)).fetchone()[0] == 1
    finally:
        w.close()

    out = client.post("/v1/forget", headers={"Authorization": f"Bearer {token}"},
                      json={"id": lid, "reason": "not a real book"})
    assert out.status_code == 200 and out.json()["retracted"] >= 1

    w = open_writer(settings.db.path)
    try:
        left = w.execute("SELECT COUNT(*) FROM vec_library WHERE ref_id=?",
                         (row_id,)).fetchone()[0]
    finally:
        w.close()
    assert left == 0, "a forgotten book chunk left its vector in vec_library"
