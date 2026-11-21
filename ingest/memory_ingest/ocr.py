from __future__ import annotations

import base64
import logging
import time

import httpx

log = logging.getLogger("memory_ingest.ocr")


class OcrError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class OcrClient:
    def __init__(
        self,
        base_url: str,
        key: str,
        transport: httpx.BaseTransport | None = None,
        load_wait_s: float = 180.0,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers={"X-API-Key": key},
            timeout=httpx.Timeout(300.0, connect=15.0),
            transport=transport,
        )
        self._load_wait_s = load_wait_s

    def close(self) -> None:
        self._client.close()

    def wait_ready(self) -> None:
        'Block until /health says ok (model may be loading after restart).'
        deadline = time.monotonic() + self._load_wait_s
        while True:
            status = "unreachable"
            try:
                r = self._client.get("/health")
                if r.status_code < 400:
                    status = str(r.json().get("status"))
                else:
                    status = f"edge {r.status_code}"
            except (httpx.HTTPError, ValueError):
                pass
            if status == "ok":
                return
            if time.monotonic() > deadline:
                raise OcrError(f"OCR node not ready (status={status})")
            log.info("OCR node %s — waiting", status)
            time.sleep(5.0)

    def ocr_image(
        self, image_bytes: bytes, *, fmt: bool = False, max_new_tokens: int = 512
    ) -> str:
        try:
            r = self._client.post(
                "/ocr/base64",
                json={
                    "image": base64.b64encode(image_bytes).decode(),
                    "format": fmt,
                    "crop_to_patches": False,
                    "max_new_tokens": max_new_tokens,
                },
            )
        except httpx.HTTPError as exc:
            raise OcrError(f"OCR transport: {exc!r}", status=None) from exc
        if r.status_code >= 400:
            raise OcrError(f"OCR {r.status_code}: {r.text[:200]}", status=r.status_code)
        return str(r.json().get("text", ""))

    def _ocr_page_patient(self, page: bytes) -> str:
        try:
            return self.ocr_image(page)
        except OcrError as exc:
            if exc.status is not None and exc.status < 500:
                raise
            log.warning("ocr page failed (%s) — waiting for the node, then "
                        "retrying with a bounded plain-text budget", exc)
            self.wait_ready()
            return self.ocr_image(page, fmt=False, max_new_tokens=1536)

    def ocr_pages(self, pages: list[bytes]) -> str:
        from memory_ingest.tiling import has_content, split_for_ocr

        self.wait_ready()
        texts = []
        for i, page in enumerate(pages):
            tiles = split_for_ocr(page)
            readable = [t for t in tiles if has_content(t)]
            log.info("ocr page %d/%d (%d tile%s, %d blank skipped)",
                     i + 1, len(pages), len(tiles),
                     "" if len(tiles) == 1 else "s", len(tiles) - len(readable))
            page_parts = []
            for j, t in enumerate(readable):
                try:
                    page_parts.append(self._ocr_page_patient(t))
                except OcrError as exc:
                    log.warning("ocr tile skipped (page %d tile %d): %s",
                                i + 1, j + 1, exc)
            texts.append("\n".join(page_parts))
        return "\f".join(texts)
