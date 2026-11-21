'Shared test fixtures.'

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]


from memory_harness.config import Settings, load_settings  # noqa: E402


class FakeBgeM3:

    def __init__(self, dim: int = 1024, status: str = "ok", aliases: dict[str, str] | None = None) -> None:
        self.dim = dim
        self.status = status
        self.aliases = aliases or {}
        self.embed_calls: list[dict] = []
        self.block_passage = None
        self._slots: dict[str, int] = {}

    def transport(self):  # type: ignore[no-untyped-def]
        import httpx

        return httpx.MockTransport(self.handle)

    def handle(self, request):  # type: ignore[no-untyped-def]
        import json

        import httpx

        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={
                "status": self.status, "model": "BAAI/bge-m3",
                "device": "cuda", "dimensions": self.dim,
            })
        if path == "/embed":
            body = json.loads(request.content)
            if body.get("task") == "retrieval.passage" and self.block_passage is not None:
                self.block_passage.wait(10)
            self.embed_calls.append(body)
            vecs = [self._vec(t) for t in body["texts"]]
            return httpx.Response(200, json={
                "embeddings": vecs, "dimensions": self.dim, "model": "BAAI/bge-m3",
                "count": len(vecs), "device": "cuda", "sparse": None, "colbert": None,
            })
        if path == "/similarity":
            body = json.loads(request.content)
            sim = [
                [self._cos(self._vec(a), self._vec(b)) for b in body["texts_b"]]
                for a in body["texts_a"]
            ]
            return httpx.Response(200, json={"similarity": sim, "model": "BAAI/bge-m3"})
        return httpx.Response(404)

    def _basis(self, token: str) -> int:
        canonical = self.aliases.get(token, token)
        if canonical not in self._slots:
            self._slots[canonical] = len(self._slots) % self.dim
        return self._slots[canonical]

    def _vec(self, text: str) -> list[float]:
        import math

        acc = [0.0] * self.dim
        for tok in text.lower().split():
            acc[self._basis(tok)] += 1.0
        norm = math.sqrt(sum(x * x for x in acc)) or 1.0
        return [x / norm for x in acc]

    @staticmethod
    def _cos(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b))


@pytest.fixture
def fake_bge():  # type: ignore[no-untyped-def]
    return FakeBgeM3()


@pytest.fixture
def embeddings_client(fake_bge):  # type: ignore[no-untyped-def]
    from memory_harness.clients.embeddings import EmbeddingsClient

    return EmbeddingsClient(
        "http://embed.test", "test-key", transport=fake_bge.transport(), dim=fake_bge.dim
    )


class FakeChatModel:

    def __init__(self, content: str = "[]", reasoning: str = "long chain of thought") -> None:
        self.content = content
        self.reasoning = reasoning
        self.requests: list[dict] = []
        self.queue: list[str] = []

    def transport(self):  # type: ignore[no-untyped-def]
        import httpx

        return httpx.MockTransport(self.handle)

    def handle(self, request):  # type: ignore[no-untyped-def]
        import json

        import httpx

        if request.url.path.endswith("/chat/completions"):
            self.requests.append(json.loads(request.content))
            content = self.queue.pop(0) if self.queue else self.content
            return httpx.Response(200, json={
                "choices": [{"message": {
                    "content": content,
                    "reasoning_content": self.reasoning,
                }}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            })
        return httpx.Response(404)


@pytest.fixture
def fake_llm():  # type: ignore[no-untyped-def]
    return FakeChatModel()


@pytest.fixture
def llm_client(fake_llm):  # type: ignore[no-untyped-def]
    from memory_harness.clients.llm import LlmClient

    return LlmClient(
        "http://model.test/v1", "",
        "test-key", max_tokens=8192, transport=fake_llm.transport(),
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    'Example config with db + blobs redirected into a temp dir.'
    s = Settings().model_copy(deep=True)
    s.db.path = str(tmp_path / "harness.db")
    s.db.blob_dir = str(tmp_path / "blobs")
    return s


@pytest.fixture
def client(settings: Settings) -> TestClient:
    'TestClient over a fresh app (lifespan runs migrations + emotion seed).'
    from memory_harness.main import create_app

    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def wcon(tmp_path: Path):  # type: ignore[no-untyped-def]
    'Fresh writer connection: temp WAL DB, migrations applied, emotions seeded.'
    from memory_harness.db.connection import open_writer
    from memory_harness.db.migrate import apply_migrations
    from memory_harness.domain.emotions import seed_emotions

    con = open_writer(tmp_path / "harness.db")
    apply_migrations(con)
    seed_emotions(con)
    yield con
    con.close()


@pytest.fixture
def minter(settings: Settings):  # type: ignore[no-untyped-def]
    'Mint keys into the SAME db file the `client` app serves.'
    from memory_harness.auth.principal import mint_key
    from memory_harness.db.connection import open_writer
    from memory_harness.db.migrate import apply_migrations

    def _mint(**kwargs):  # type: ignore[no-untyped-def]
        con = open_writer(settings.db.path, settings.db.busy_timeout_ms)
        try:
            apply_migrations(con)
            token, key_id = mint_key(con, **kwargs)
            con.commit()
            return token, key_id
        finally:
            con.close()

    return _mint


