from memory_mcp.config import DEFAULT_ALLOWED_HOSTS, get_settings


def test_only_loopback_hosts_allowed_by_default() -> None:
    assert set(DEFAULT_ALLOWED_HOSTS) == {"127.0.0.1:8099", "localhost:8099"}
    assert "127.0.0.1:8099" in DEFAULT_ALLOWED_HOSTS


def test_env_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "a.example, b.example:8443")
    s = get_settings()
    assert s.allowed_hosts == ["a.example", "b.example:8443"]


def _mw(shared="shared-token", clients=None):  # type: ignore[no-untyped-def]
    from memory_mcp.server import BearerAuthMiddleware

    async def app(scope, receive, send):  # type: ignore[no-untyped-def]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return BearerAuthMiddleware(app, shared, client_tokens=clients or {})


async def _call(mw, auth, runtime=None):  # type: ignore[no-untyped-def]
    hdrs = []
    if auth:
        hdrs.append((b"authorization", auth.encode()))
    if runtime:
        hdrs.append((b"x-runtime", runtime.encode()))
    sent = []

    async def send(m):  # type: ignore[no-untyped-def]
        sent.append(m)

    async def receive():  # type: ignore[no-untyped-def]
        return {"type": "http.request", "body": b""}

    await mw({"type": "http", "path": "/mcp", "headers": hdrs}, receive, send)
    return sent[0]["status"]


async def test_a_named_client_token_is_accepted() -> None:
    "Hermes gets its own door key, not a copy of everyone else's."
    mw = _mw(clients={"hermes": "hermes-secret"})
    assert await _call(mw, "Bearer hermes-secret") == 200


async def test_revoking_one_client_leaves_the_others_working() -> None:
    'The whole point of naming them.'
    mw = _mw(clients={"hermes": "hermes-secret", "phone": "phone-secret"})
    assert await _call(mw, "Bearer hermes-secret") == 200
    mw2 = _mw(clients={"phone": "phone-secret"})
    assert await _call(mw2, "Bearer hermes-secret") == 401
    assert await _call(mw2, "Bearer phone-secret") == 200


async def test_an_unknown_token_is_still_refused() -> None:
    mw = _mw(clients={"hermes": "hermes-secret"})
    assert await _call(mw, "Bearer not-a-token") == 401
    assert await _call(mw, None) == 401


async def test_the_credential_names_the_runtime_over_the_header() -> None:
    '`X-Runtime` is a header the client picks for itself. The token is not.'
    from memory_mcp.runtime import current_runtime

    mw = _mw(clients={"hermes": "hermes-secret"})
    await _call(mw, "Bearer hermes-secret", runtime="definitely-not-hermes")
    assert current_runtime() == "hermes"


async def test_the_shared_token_still_works_for_existing_callers() -> None:
    'Nothing that already connects may break the day this lands.'
    mw = _mw(clients={"hermes": "hermes-secret"})
    assert await _call(mw, "Bearer shared-token") == 200


