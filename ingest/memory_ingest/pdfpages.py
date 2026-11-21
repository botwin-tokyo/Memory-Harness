'PDF → per-page PNGs, rendered WIDTH-FIT for the OCR node.'

from __future__ import annotations

import fitz

MAX_SIDE = 1024
MAX_SCALE = 4.0


def rasterize_page(page: "fitz.Page") -> bytes:
    "One already-open page → a PNG at the node's input width."
    rect = page.rect
    scale = min(MAX_SIDE / max(rect.width, 1.0), MAX_SCALE)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
    return bytes(pix.tobytes("png"))


def rasterize(pdf_bytes: bytes) -> list[bytes]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [rasterize_page(page) for page in doc]
    finally:
        doc.close()
