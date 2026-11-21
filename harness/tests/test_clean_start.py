from memory_harness.db.connection import open_reader


def test_clean_start_and_local_recall(client, settings, minter):
    with open_reader(settings.db.path) as db:
        for table in ("facts", "episodes", "preferences", "notes", "procedures",
                      "core_memory", "identities", "entity_aliases", "events", "policy"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert client.app.state.fleet is None
    assert client.app.state.worker_names == ["decay_worker"]
    token, _ = minter(principal_id="test", node="local", allowed_scopes=["shared"],
                      home_scope="shared", trust_ceiling="user")
    headers = {"Authorization": f"Bearer {token}"}
    text = "The synthetic calibration sample is violet."
    write = client.post("/v1/remember", headers=headers, json={
        "type": "fact", "body": text, "emotion": "Neutrality"})
    assert write.status_code == 200
    recall = client.post("/v1/recall", headers=headers, json={"query": "calibration"})
    assert recall.status_code == 200
    assert any(item["text"] == text for item in recall.json()["facts"])
