from __future__ import annotations

import logging

import httpx
import pytest

from memory_mcp import server as srv
from memory_mcp import tools
from memory_mcp.runtime import current_runtime, set_runtime


@pytest.fixture
async def app_client(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(srv.settings, "mcp_access_token", "sekrit")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(srv.create_app()), base_url="http://mcp.test"
    ) as client:
        yield client


MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


async def test_missing_token_401(app_client) -> None:  # type: ignore[no-untyped-def]
    r = await app_client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS)
    assert r.status_code == 401


async def test_wrong_token_401(app_client) -> None:  # type: ignore[no-untyped-def]
    r = await app_client.post(
        "/mcp", json=LIST_TOOLS,
        headers={**MCP_HEADERS, "Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


async def test_unconfigured_token_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(srv.settings, "mcp_access_token", "")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(srv.create_app()), base_url="http://mcp.test"
    ) as client:
        r = await client.post(
            "/mcp", json=LIST_TOOLS,
            headers={**MCP_HEADERS, "Authorization": "Bearer "},
        )
    assert r.status_code == 401


async def test_correct_token_reaches_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(srv.settings, "mcp_access_token", "sekrit")
    outer = srv.create_app()
    inner = outer
    while not hasattr(inner, "router"):
        inner = inner.app
    async with inner.router.lifespan_context(inner):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(outer), base_url="http://mcp.test"
        ) as client:
            r = await client.post(
                "/mcp", json=LIST_TOOLS,
                headers={**MCP_HEADERS, "Authorization": "Bearer sekrit",
                         "X-Runtime": "hermes"},
            )
    assert r.status_code != 401


async def test_healthz_needs_no_auth(app_client) -> None:  # type: ignore[no-untyped-def]
    r = await app_client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_write_logs_runtime_but_memory_is_clean(  # type: ignore[no-untyped-def]
    mock_harness, caplog: pytest.LogCaptureFixture
) -> None:
    set_runtime("hermes")
    try:
        with caplog.at_level(logging.INFO, logger="memory_mcp.tools"):
            await tools.remember(
                type="fact", body="Companion lectures on Tuesdays",
                emotion="Interest", subject="companion", tags=["schedule"],
            )
        assert any(
            "mcp_write" in rec.getMessage() and "runtime=hermes" in rec.getMessage()
            for rec in caplog.records
        )
    finally:
        set_runtime("unknown")
    sent = mock_harness.requests[0]["json"]
    assert "hermes" not in str(sent).lower()
    assert "runtime" not in str(sent).lower()
    assert "author" not in sent and "principal" not in str(sent).lower()
    assert mock_harness.requests[0]["auth"] == "Bearer test-key"


def test_runtime_contextvar_default() -> None:
    assert current_runtime() == "unknown"
