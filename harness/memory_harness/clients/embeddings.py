from __future__ import annotations

import threading
from typing import Any

import httpx


class EmbeddingsUnavailable(Exception):
    pass


class DimensionMismatch(Exception):
    pass


def _chunks(items: list[str], n: int) -> list[list[str]]:
    return [items[i : i + n] for i in range(0, len(items), n)]


class EmbeddingsClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_s: float = 30.0,
        dim: int = 1024,
        max_batch: int = 128,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._dim = dim
        self._max_batch = max_batch
        self._has_key = bool(api_key)
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key},
            timeout=timeout_s,
            transport=transport,
        )
        self._doc_gate = threading.Semaphore(1)

    def health(self) -> dict[str, Any]:
        'GET /health (no auth required by the endpoint).'
        try:
            r = self._client.get("/health")
            r.raise_for_status()
            return dict(r.json())
        except httpx.HTTPError as e:
            raise EmbeddingsUnavailable(str(e)) from e

    def available(self) -> bool:
        "True only when configured AND the model is loaded (status == 'ok')."
        if not self._has_key:
            return False
        try:
            return self.health().get("status") == "ok"
        except EmbeddingsUnavailable:
            return False

    def _embed(self, texts: list[str], task: str) -> list[list[float]]:
        r = self._client.post(
            "/embed",
            json={"texts": texts, "task": task, "normalize": True},
        )
        r.raise_for_status()
        body = r.json()
        if int(body["dimensions"]) != self._dim:
            raise DimensionMismatch(
                f"endpoint dim {body['dimensions']} != configured {self._dim}"
            )
        vectors = body["embeddings"]
        for v in vectors:
            if len(v) != self._dim:
                raise DimensionMismatch(f"vector len {len(v)} != {self._dim}")
        return [[float(x) for x in v] for v in vectors]

    def embed_query(self, text: str) -> list[float] | None:
        'Priority path (D39): never waits behind document batches.'
        try:
            if not self.available():
                return None
            return self._embed([text], "retrieval.query")[0]
        except (httpx.HTTPError, EmbeddingsUnavailable):
            return None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        'Write-path embeds: retrieval.passage, chunked at <= max_batch/call.'
        if not texts:
            return []
        if not self.available():
            raise EmbeddingsUnavailable("embedding service down or loading")
        out: list[list[float]] = []
        with self._doc_gate:
            for chunk in _chunks(texts, self._max_batch):
                try:
                    out.extend(self._embed(chunk, "retrieval.passage"))
                except httpx.HTTPError as e:
                    raise EmbeddingsUnavailable(str(e)) from e
        return out

    def similarity(self, texts_a: list[str], texts_b: list[str]) -> list[list[float]]:
        "Pairwise cosine similarity (curator's link dedup / entity matching)."
        try:
            r = self._client.post(
                "/similarity", json={"texts_a": texts_a, "texts_b": texts_b}
            )
            r.raise_for_status()
            return [[float(x) for x in row] for row in r.json()["similarity"]]
        except httpx.HTTPError as e:
            raise EmbeddingsUnavailable(str(e)) from e

    def close(self) -> None:
        self._client.close()
