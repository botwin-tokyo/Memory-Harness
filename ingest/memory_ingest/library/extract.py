'A PDF book → its pages, cheaply.'

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

log = logging.getLogger("memory_ingest.library.extract")

MIN_TEXT_CHARS = 120

PAGE_SEP = "\f"


class UnreadableBook(RuntimeError):
    'The PDF cannot be opened, or is encrypted.'


@dataclass
class BookPage:
    number: int
    text: str
    ocr: bool = False

    @property
    def chars(self) -> int:
        return len(self.text.strip())


@dataclass
class BookExtract:
    pages: list[BookPage] = field(default_factory=list)
    title: str | None = None
    author: str | None = None
    published: str | None = None
    pdf_metadata: dict[str, str] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def ocr_pages(self) -> int:
        return sum(1 for p in self.pages if p.ocr)

    @property
    def empty_pages(self) -> int:
        return sum(1 for p in self.pages if not p.chars)

    def as_report(self) -> dict[str, object]:
        return {
            "pages": self.page_count,
            "ocr_pages": self.ocr_pages,
            "empty_pages": self.empty_pages,
            "chars": sum(p.chars for p in self.pages),
            "title": self.title,
            "author": self.author,
            "published": self.published,
        }

    def text(self) -> str:
        'The whole book, page breaks preserved for the chunker.'
        return PAGE_SEP.join(p.text for p in self.pages)


_WS = re.compile(r"[ \t]+")
_NL = re.compile(r"\n{3,}")


def clean_page(text: str) -> str:
    'Collapse the whitespace a PDF text layer is full of, keep paragraphs.'
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS.sub(" ", text)
    text = _NL.sub("\n\n", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def _metadata(doc: object) -> dict[str, str]:
    raw = getattr(doc, "metadata", None) or {}
    out: dict[str, str] = {}
    for key in ("title", "author", "creationDate", "producer", "subject"):
        value = raw.get(key)
        if value and str(value).strip():
            out[key] = str(value).strip()
    return out


def _published_from(meta: dict[str, str]) -> str | None:
    'PDF dates look like `D:20140312...`; keep just the year.'
    raw = meta.get("creationDate", "")
    m = re.search(r"(\d{4})", raw)
    return m.group(1) if m else None


def extract_book(
    path: str | Path,
    *,
    ocr_pages: Callable[[Sequence[bytes]], str] | None = None,
    rasterize_page: Callable[[object], bytes] | None = None,
    min_text_chars: int = MIN_TEXT_CHARS,
    on_page: Callable[[int, int, bool], None] | None = None,
) -> BookExtract:
    'Read every page. OCR only the pages with no usable text layer.'
    import fitz

    p = Path(path).expanduser()
    if not p.is_file():
        raise UnreadableBook(f"no such file: {p}")
    try:
        doc = fitz.open(p)
    except Exception as exc:  # noqa: BLE001 - any fitz failure is unreadable
        raise UnreadableBook(f"cannot open {p.name}: {exc}") from exc

    if getattr(doc, "needs_pass", False) or getattr(doc, "is_encrypted", False):
        raise UnreadableBook(
            f"{p.name} is encrypted — decrypt it before importing, rather than "
            "importing a book we can only half-read")

    meta = _metadata(doc)
    out = BookExtract(
        title=meta.get("title"),
        author=meta.get("author"),
        published=_published_from(meta),
        pdf_metadata=meta,
    )

    total = doc.page_count
    for index, page in enumerate(doc):
        number = index + 1
        try:
            text = clean_page(page.get_text() or "")
        except Exception as exc:  # noqa: BLE001 - one bad page, not the book
            log.warning("page %d text layer failed: %s", number, exc)
            text = ""

        if len(text) >= min_text_chars or ocr_pages is None:
            out.pages.append(BookPage(number=number, text=text))
            if on_page:
                on_page(number, total, False)
            continue

        try:
            image = rasterize_page(page) if rasterize_page else None
            ocr_text = clean_page(ocr_pages([image]) if image else "")
        except Exception as exc:  # noqa: BLE001
            log.warning("page %d OCR failed: %s", number, exc)
            ocr_text = ""
        best = ocr_text if len(ocr_text) > len(text) else text
        out.pages.append(BookPage(number=number, text=best, ocr=bool(ocr_text)))
        if on_page:
            on_page(number, total, bool(ocr_text))

    doc.close()
    log.info("%s: %d pages, %d needed OCR, %d empty", p.name,
             out.page_count, out.ocr_pages, out.empty_pages)
    return out


def slugify_title(title: str, max_len: int = 60) -> str:
    "`Sample Book` → `sample-book`. The book's identity in tags and ledger."
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")
    return slug[:max_len] or "untitled"
