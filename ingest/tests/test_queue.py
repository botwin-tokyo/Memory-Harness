from __future__ import annotations

import threading
import time
import types

import httpx
import pytest

from memory_ingest import server as srv
from memory_ingest.ocr import OcrClient, OcrError
from memory_ingest.pipeline import IngestResult


class SlowStubIngestor:
    'Counts concurrent ingest() calls; sleeps to force overlap attempts.'

    active = 0
    max_active = 0
    lock = threading.Lock()

    def __init__(self) -> None:
        self.writer = types.SimpleNamespace(close=lambda: None)
        self.ocr = types.SimpleNamespace(close=lambda: None)
        self.state = types.SimpleNamespace(close=lambda: None)

    def ingest(self, source, **kw):  # type: ignore[no-untyped-def]
        with SlowStubIngestor.lock:
            SlowStubIngestor.active += 1
            SlowStubIngestor.max_active = max(
                SlowStubIngestor.max_active, SlowStubIngestor.active
            )
        time.sleep(0.25)
        with SlowStubIngestor.lock:
            SlowStubIngestor.active -= 1
        return IngestResult(
            entity_id="document:x", parent_lineage=f"lin-{source[-1]}",
            chunk_lineages=["c1"], route="text",
        )


async def test_concurrent_ingests_serialize(monkeypatch: pytest.MonkeyPatch) -> None:
    SlowStubIngestor.max_active = 0
    monkeypatch.setattr(srv, "build_ingestor", SlowStubIngestor)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(srv.app), base_url="http://ingest.test",
        timeout=10,
    ) as client:
        import asyncio

        r1, r2, health = await asyncio.gather(
            client.post("/ingest", json={"source": "/tmp/a"}),
            client.post("/ingest", json={"source": "/tmp/b"}),
            _poll_health_mid_run(client),
        )
    assert r1.status_code == 200 and r2.status_code == 200
    assert SlowStubIngestor.max_active == 1
    assert health["status"] == "ok"
    assert "busy" in health and "queued" in health


async def _poll_health_mid_run(client: httpx.AsyncClient) -> dict:
    import asyncio

    await asyncio.sleep(0.1)
    r = await client.get("/healthz")
    return r.json()  # type: ignore[no-any-return]


def test_ocr_retries_transient_5xx_once() -> None:
    calls = {"ocr": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        calls["ocr"] += 1
        if calls["ocr"] == 1:
            return httpx.Response(502, text="edge sad")
        return httpx.Response(200, json={"text": "recovered page text"})

    ocr = OcrClient("http://ocr.test", "k", transport=httpx.MockTransport(handler),
                    load_wait_s=5)
    assert ocr.ocr_pages([_tiny_png()]) == "recovered page text"
    assert calls["ocr"] == 2
    ocr.close()


def test_ocr_never_retries_client_errors() -> None:
    calls = {"ocr": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        calls["ocr"] += 1
        return httpx.Response(413, json={"detail": "too large"})

    ocr = OcrClient("http://ocr.test", "k", transport=httpx.MockTransport(handler))
    text = ocr.ocr_pages([_tiny_png()])
    assert calls["ocr"] == 1
    assert text == ""
    ocr.close()

    ocr2 = OcrClient("http://ocr.test", "k", transport=httpx.MockTransport(handler))
    with pytest.raises(OcrError):
        ocr2.ocr_image(_tiny_png())
    ocr2.close()


def _tiny_png() -> bytes:
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (64, 64), "white")
    ImageDraw.Draw(img).rectangle((8, 8, 56, 40), fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_default_budget_is_bounded_for_the_tunnel() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        import json as _json

        seen.append(_json.loads(request.content))
        return httpx.Response(200, json={"text": "ok"})

    ocr = OcrClient("http://ocr.test", "k", transport=httpx.MockTransport(handler))
    ocr.ocr_pages([_tiny_png()])
    ocr.close()
    assert seen[0]["format"] is False
    assert seen[0]["max_new_tokens"] <= 1536
    assert seen[0]["crop_to_patches"] is False
