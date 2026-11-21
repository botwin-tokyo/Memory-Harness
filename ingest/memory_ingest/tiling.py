'Auto-rescale + band tiling: every image reaches the OCR node clean.'

from __future__ import annotations

import io

from PIL import Image

MAX_SIDE = 1024
_CUT_SEARCH = 200
_WHITE_ENOUGH = 248
_OVERLAP = 32


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _row_means(gray: Image.Image) -> list[int]:
    return list(gray.resize((1, gray.size[1]), Image.Resampling.BOX).getdata())


def _seam_row(means: list[int], lo: int, hi: int) -> int | None:
    'The whitest row in [lo, hi), if it is white enough to be a seam.'
    if hi <= lo:
        return None
    window = means[lo:hi]
    best = max(range(len(window)), key=window.__getitem__)
    return lo + best if window[best] >= _WHITE_ENOUGH else None


def split_for_ocr(image_bytes: bytes) -> list[bytes]:
    'One page/image in → OCR-ready tiles out (each ≤1024×1024, PNG).'
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    if w > MAX_SIDE:
        h = round(h * MAX_SIDE / w)
        img = img.resize((MAX_SIDE, h), Image.Resampling.LANCZOS)
        w = MAX_SIDE
    if h <= MAX_SIDE:
        return [_png(img)]

    means = _row_means(img.convert("L"))
    tiles: list[bytes] = []
    top = 0
    while h - top > MAX_SIDE:
        target = top + MAX_SIDE
        seam = _seam_row(means, max(top + MAX_SIDE // 2, target - _CUT_SEARCH), target)
        if seam is not None and seam > top:
            tiles.append(_png(img.crop((0, top, w, seam))))
            top = seam
        else:
            tiles.append(_png(img.crop((0, top, w, target))))
            top = target - _OVERLAP
    tiles.append(_png(img.crop((0, top, w, h))))
    return tiles


def has_content(image_bytes: bytes, min_dark_px: int = 120) -> bool:
    gray = Image.open(io.BytesIO(image_bytes)).convert("L")
    hist = gray.histogram()
    return sum(hist[:160]) >= min_dark_px
