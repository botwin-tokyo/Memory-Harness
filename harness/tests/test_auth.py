from memory_harness.auth.principal import mint_key, resolve, revoke_key
from memory_harness.db.connection import open_reader, open_writer


def test_minted_key_resolves_exact_row(wcon) -> None:  # type: ignore[no-untyped-def]
    token, key_id = mint_key(
        wcon,
        principal_id="agent-a",
        node="node-1",
        allowed_scopes=["project:atlas", "shared"],
        trust_ceiling="agent",
        home_scope="project:atlas",
        link_authority="active",
        secret_mode="train-raw",
        learning_eligible_default=False,
    )
    p = resolve(wcon, token)
    assert p is not None
    assert p.principal_key_id == key_id
    assert p.principal_id == "agent-a"
    assert p.node == "node-1"
    assert p.allowed_scopes == ["project:atlas", "shared"]
    assert p.trust_ceiling == "agent"
    assert p.home_scope == "project:atlas"
    assert p.link_authority == "active"
    assert p.secret_mode == "train-raw"
    assert p.learning_eligible_default is False


def test_raw_token_never_stored(wcon) -> None:  # type: ignore[no-untyped-def]
    token, _ = mint_key(wcon, "a", "n", ["shared"])
    hashes = [r[0] for r in wcon.execute("SELECT key_hash FROM policy").fetchall()]
    assert token not in hashes
    assert all(len(h) == 64 for h in hashes)


def test_revoked_key_401(client, minter, settings) -> None:  # type: ignore[no-untyped-def]
    token, key_id = minter(principal_id="agent-b", node="node-1", allowed_scopes=["shared"])
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/v1/emotions", headers=headers).status_code == 200

    con = open_reader(settings.db.path)
    w = open_writer(settings.db.path)
    assert revoke_key(w, key_id) is True
    w.close()
    row = con.execute("SELECT revoked_at FROM policy WHERE principal_key_id=?", (key_id,)).fetchone()
    assert row[0] is not None
    con.close()

    assert client.get("/v1/emotions", headers=headers).status_code == 401


def test_garbage_key_401(client, minter) -> None:  # type: ignore[no-untyped-def]
    minter(principal_id="agent-c", node="node-1", allowed_scopes=["shared"])
    assert client.get("/v1/emotions", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_missing_key_401(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/v1/emotions").status_code == 401


def test_defaults(wcon) -> None:  # type: ignore[no-untyped-def]
    token, _ = mint_key(wcon, "agent-d", "node-2", ["project:x"])
    p = resolve(wcon, token)
    assert p is not None
    assert p.trust_ceiling == "agent"
    assert p.home_scope == "private:agent-d"
    assert p.link_authority == "propose"
    assert p.secret_mode == "store-but-dont-train"
    assert p.learning_eligible_default is True


def test_a_read_only_key_may_read_but_never_write(client, minter) -> None:  # type: ignore[no-untyped-def]
    ro, _ = minter(principal_id="reader", node="test-reader",
                   allowed_scopes=["shared"], trust_ceiling="agent",
                   home_scope="shared", read_only=True)
    h = {"Authorization": f"Bearer {ro}"}

    assert client.get("/v1/graph?fields=minimal", headers=h).status_code == 200
    assert client.post("/v1/recall", json={"query": "anything"}, headers=h).status_code == 200

    for method, path, body in [
        ("post", "/v1/remember",
         {"type": "fact", "body": "should never land", "emotion": "Neutrality"}),
        ("post", "/v1/forget", {"id": "whatever", "reason": "no"}),
        ("post", "/v1/link", {"src_lineage_id": "a", "dst_lineage_id": "b",
                              "link_type": "relates_to"}),
        ("post", "/v1/intake", {"body": "x"}),
        ("post", "/v1/resolve", {"winner_fact_id": "a", "loser_fact_id": "b"}),
        ("post", "/v1/core", {"op": "append", "slot_key": "identity",
                              "value": "x", "owner_scope": "shared"}),
        ("post", "/v1/events", {"event_type": "user_msg", "body": "x"}),
        ("post", "/v1/entities", {"kind": "person", "name": "X"}),
    ]:
        r = getattr(client, method)(path, json=body, headers=h)
        assert r.status_code == 403, f"{path} was NOT refused (got {r.status_code})"
        assert "read-only" in r.json()["detail"]


def test_read_only_is_deny_by_default_for_unknown_write_routes(client, minter) -> None:  # type: ignore[no-untyped-def]
    'A route added tomorrow must be refused without anyone remembering to.'
    from memory_harness.auth.deps import _is_mutating

    assert _is_mutating("POST", "/v1/some-route-invented-next-year") is True
    assert _is_mutating("PUT", "/v1/anything") is True
    assert _is_mutating("DELETE", "/v1/anything") is True
    assert _is_mutating("GET", "/v1/graph") is False
    assert _is_mutating("POST", "/v1/recall") is False
