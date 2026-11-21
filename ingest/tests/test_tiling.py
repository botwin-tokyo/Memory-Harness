from __future__ import annotations

import io

import httpx
from PIL import Image, ImageDraw

from memory_ingest.ocr import OcrClient
from memory_ingest.pdfpages import rasterize
from memory_ingest.tiling import MAX_SIDE, split_for_ocr
from tests.conftest import make_pdf


def png_of(width: int, height: int, bands: list[tuple[int, int]] | None = None) -> bytes:
    'A white image with black text-like bands at [(top, bottom), …].'
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    for top, bottom in bands or []:
        d.rectangle((10, top, width - 10, bottom), fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def dims(png: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(png)).size


def test_small_image_passes_through() -> None:
    tiles = split_for_ocr(png_of(600, 400))
    assert len(tiles) == 1 and dims(tiles[0]) == (600, 400)


def test_wide_image_downscales_to_width_limit() -> None:
    tiles = split_for_ocr(png_of(2048, 700))
    assert len(tiles) == 1
    w, h = dims(tiles[0])
    assert w == MAX_SIDE and h == 350


def test_tall_page_splits_at_whitespace_seams() -> None:
    bands = [(i * 90, i * 90 + 60) for i in range(32) if not (10 <= i <= 11 or 21 <= i <= 22)]
    tiles = split_for_ocr(png_of(800, 2900, bands))
    assert len(tiles) >= 3
    total = 0
    for t in tiles:
        w, h = dims(t)
        assert w == 800 and h <= MAX_SIDE
        total += h
    assert total == 2900


def test_dense_page_falls_back_to_overlap() -> None:
    tiles = split_for_ocr(png_of(800, 2200, [(0, 2200)]))
    assert all(dims(t)[1] <= MAX_SIDE for t in tiles)
    assert sum(dims(t)[1] for t in tiles) > 2200


def test_rasterize_width_fits() -> None:
    import fitz

    pdf = make_pdf(["page one"])
    rect = fitz.open(stream=pdf, filetype="pdf")[0].rect
    w, h = dims(rasterize(pdf)[0])
    assert w == MAX_SIDE
    assert h > MAX_SIDE
    assert abs(h - rect.height * (MAX_SIDE / rect.width)) <= 1


def test_ocr_client_tiles_tall_pages() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        calls["n"] += 1
        return httpx.Response(200, json={"text": f"tile {calls['n']}"})

    ocr = OcrClient("http://ocr.test", "k", transport=httpx.MockTransport(handler))
    text = ocr.ocr_pages([png_of(800, 2900, [(i * 90, i * 90 + 60) for i in range(30)])])
    ocr.close()
    assert calls["n"] >= 3
    assert "tile 1\ntile 2" in text
    assert "\f" not in text


def test_blank_tiles_never_reach_the_node() -> None:
    from memory_ingest.tiling import has_content

    assert not has_content(png_of(800, 600))
    assert has_content(png_of(800, 600, [(100, 160)]))

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        calls["n"] += 1
        return httpx.Response(200, json={"text": "content"})

    ocr = OcrClient("http://ocr.test", "k", transport=httpx.MockTransport(handler))
    text = ocr.ocr_pages([png_of(800, 2900, [(i * 90, i * 90 + 60) for i in range(9)])])
    ocr.close()
    assert calls["n"] < 3
    assert "content" in text
