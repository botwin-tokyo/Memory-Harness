'Shared fixtures: a mock harness behind the real Harness client.'

from __future__ import annotations

import json as jsonlib
from typing import Any

import httpx
import pytest

from memory_mcp.harness import Harness

CANNED: dict[tuple[str, str], Any] = {
    ("GET", "/v1/whoami"): {
        "principal_id": "self", "node": "node-mcp", "trust_ceiling": "agent",
        "allowed_scopes": ["shared"], "home_scope": "shared",
        "learning_eligible_default": True,
        "secret_mode": "store-but-dont-train", "link_authority": "propose",
    },
    ("GET", "/v1/emotions"): [
        {"emotion_id": i, "name": f"E{i}", "family": "f", "description": "d"}
        for i in range(1, 128)
    ],
    ("POST", "/v1/recall"): {
        "goal": None, "core": [], "preferences": [], "facts": [], "episodes": [],
        "procedures": [], "identities": {
            "self": {"display": "Assistant", "pronouns": "she/her", "perspective": "first_person"},
            "companion": {"display": "Companion", "pronouns": "he/him", "perspective": "second_person"},
        }, "open_questions": [], "token_estimate": 10,
    },
    ("POST", "/v1/remember"): {"lineage_id": "lin-1", "revision": 1, "ref": "lin-1.1", "superseded": None},
    ("POST", "/v1/intake"): {"lineage_id": "lin-2", "revision": 1, "ref": "lin-2.1",
                             "superseded": None, "links": []},
    ("POST", "/v1/explore"): {"start_id": "lin-1", "depth": 2, "items": []},
    ("GET", "/v1/history/lin-1"): {"lineage_id": "lin-1", "type": "fact", "revisions": []},
    ("POST", "/v1/link"): {"link_id": "lk-1", "link_type": "relates_to",
                           "target": "lin-2", "status": "proposed"},
    ("POST", "/v1/forget"): {"retracted": 1},
}


class MockHarness:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = jsonlib.loads(request.content) if request.content else None
        self.requests.append({
            "method": request.method,
            "path": request.url.path,
            "json": body,
            "auth": request.headers.get("Authorization"),
        })
        if request.url.path in ("/v1/remember", "/v1/intake") and not (body or {}).get("emotion"):
            return httpx.Response(400, json={"detail": "emotion is required"})
        canned = CANNED.get((request.method, request.url.path))
        if canned is None:
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json=canned)


@pytest.fixture
def mock_harness(monkeypatch: pytest.MonkeyPatch) -> MockHarness:
    from memory_mcp import server as srv

    mock = MockHarness()
    client = Harness(
        "http://harness.test", "test-key",
        transport=httpx.MockTransport(mock.handler),
    )
    monkeypatch.setattr(srv, "harness", client)
    return mock
