'An uploaded PDF → a book in the library. One sweep, resumable.'

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from memory_ingest.library.extract import UnreadableBook, extract_book, slugify_title
from memory_ingest.library.writer import BookLedger, BookMeta, BookProgress, BookWriter
from memory_ingest.writer import HarnessWriter

log = logging.getLogger("memory_ingest.library.service")

MAX_BOOK_BYTES = 200 * 1024 * 1024
PDF_MAGIC = b"%PDF-"


class BookRefused(ValueError):
    'The upload is not something we will accept.'


@dataclass
class StoredBook:
    path: Path
    sha: str
    size: int


def library_dir(settings: object) -> Path:
    root = Path(getattr(settings, "library_dir", "") or
                ".state/library").expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def store_upload(tmp_path: Path, settings: object) -> StoredBook:
    'Validate the streamed file and file it under its own sha.'
    size = tmp_path.stat().st_size
    if size == 0:
        raise BookRefused("empty upload")
    if size > MAX_BOOK_BYTES:
        raise BookRefused(
            f"{size / 1e6:.0f}MB exceeds the {MAX_BOOK_BYTES / 1e6:.0f}MB limit")
    with tmp_path.open("rb") as fh:
        if fh.read(len(PDF_MAGIC)) != PDF_MAGIC:
            raise BookRefused("not a PDF (no %PDF- header)")

    h = hashlib.sha256()
    with tmp_path.open("rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    sha = h.hexdigest()

    target = library_dir(settings) / f"{sha[:32]}.pdf"
    if not target.exists():
        shutil.move(str(tmp_path), target)
    else:
        tmp_path.unlink(missing_ok=True)
    return StoredBook(path=target, sha=sha[:32], size=size)


def ingest_book(
    stored: StoredBook,
    *,
    title: str,
    author: str | None = None,
    published: str | None = None,
    synopsis: str | None = None,
    slug: str | None = None,
    writer: HarnessWriter,
    ledger: BookLedger,
    ocr_pages=None,  # type: ignore[no-untyped-def]
    rasterize_page=None,  # type: ignore[no-untyped-def]
    progress: BookProgress | None = None,
) -> BookProgress:
    "Extract, then write. The operator's metadata wins over the PDF's."
    p = progress or BookProgress(slug=slug or "")

    def read_page(number: int, total: int, used_ocr: bool) -> None:
        p.phase = "extracting"
        p.current_page = number
        p.total_pages = total
        if used_ocr:
            p.ocr_pages += 1

    try:
        extract = extract_book(stored.path, ocr_pages=ocr_pages,
                               rasterize_page=rasterize_page,
                               on_page=read_page)
    except UnreadableBook as exc:
        raise BookRefused(str(exc)) from exc

    meta = BookMeta(
        slug=slug or slugify_title(title or extract.title or stored.sha),
        title=title or extract.title or "untitled",
        author=author or extract.author,
        published=published or extract.published,
        synopsis=synopsis,
        source_sha=stored.sha,
    )
    log.info("book %s: %d pages (%d need OCR)", meta.slug,
             extract.page_count, extract.ocr_pages)
    return BookWriter(writer, ledger).write(meta, extract, p)
