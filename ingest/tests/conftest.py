from __future__ import annotations

import http.server
import json
import threading
from typing import Any

import fitz
import httpx
import pytest

from memory_ingest.config import Settings
from memory_ingest.ocr import OcrClient
from memory_ingest.pipeline import Ingestor
from memory_ingest.state import StateDB
from memory_ingest.writer import HarnessWriter


class MockHarness:
    'Answers the four endpoints the writer uses; records every request.'

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._seq = 0

    def _lin(self) -> str:
        self._seq += 1
        return f"lin-{self._seq:04d}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        rec = {"path": request.url.path, "json": body,
               "auth": request.headers.get("Authorization")}
        self.requests.append(rec)
        if request.url.path == "/v1/entities":
            return httpx.Response(200, json={
                "id": f"{body['kind']}:{body['slug']}",
                "display": body["name"], "subject_class": "world"})
        if request.url.path == "/v1/intake":
            lin = body.get("memory_id") or self._lin()
            rev = 2 if body.get("memory_id") else 1
            return httpx.Response(200, json={
                "lineage_id": lin, "revision": rev,
                "ref": f"{lin}.{rev}", "superseded": None, "links": []})
        if request.url.path == "/v1/link":
            return httpx.Response(200, json={
                "link_id": self._lin(), "link_type": body["link_type"],
                "target": body["dst_lineage_id"], "status": "active"})
        if request.url.path == "/v1/forget":
            return httpx.Response(200, json={"retracted": 1})
        return httpx.Response(404, json={"detail": "not found"})

    def by_path(self, path: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["path"] == path]


class FakeOcr(OcrClient):
    'Returns predictable text per page; records page counts.'

    def __init__(self) -> None:
        self.page_batches: list[int] = []

    def close(self) -> None:
        pass

    def ocr_pages(self, pages: list[bytes]) -> str:
        assert all(isinstance(p, bytes) and p for p in pages)
        self.page_batches.append(len(pages))
        return "\f".join(
            f"OCR PAGE {i + 1}: recognized text from a rasterized page image."
            for i in range(len(pages))
        )


def make_pdf(pages: list[str]) -> bytes:
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def settings(tmp_path) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(
        harness_base="http://harness.test",
        harness_key="ingest-key",
        ocr_base="http://ocr.test",
        ocr_key="ocr-key",
        state_db=str(tmp_path / "state.db"),
        allow_private_fetch=True,
    )


@pytest.fixture
def mock_harness() -> MockHarness:
    return MockHarness()


@pytest.fixture
def fake_ocr() -> FakeOcr:
    return FakeOcr()


@pytest.fixture
def ingestor(settings, mock_harness, fake_ocr):  # type: ignore[no-untyped-def]
    writer = HarnessWriter(
        settings.harness_base, settings.harness_key,
        transport=httpx.MockTransport(mock_harness.handler),
    )
    ing = Ingestor(
        settings, writer, fake_ocr, StateDB(settings.state_db),
        renderer=lambda url, s: make_pdf(["RENDERED PAGE ONE", "RENDERED PAGE TWO"]),
    )
    yield ing
    writer.close()
    ing.state.close()


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def http_fixture_server(tmp_path):  # type: ignore[no-untyped-def]
    'Serves tmp_path/www on 127.0.0.1:<port>; yields the base url.'
    www = tmp_path / "www"
    www.mkdir()

    handler = lambda *a, **kw: _Quiet(*a, directory=str(www), **kw)  # noqa: E731
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield www, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
