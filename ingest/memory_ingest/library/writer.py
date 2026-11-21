'A book → its subtree in the library, and a ledger so a kill can resume.'

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from memory_contract import (
    BOOK_CHUNK_TOKENS,
    LIBRARY_SCOPE,
    PART_OF_LINK,
    book_chunk_tags,
    book_tags,
)

from memory_ingest.chunker import chunk_text
from memory_ingest.library.extract import BookExtract, BookPage
from memory_ingest.state import StateDB
from memory_ingest.writer import HarnessWriter

log = logging.getLogger("memory_ingest.library.writer")


@dataclass
class BookMeta:
    "What the operator's app sends. The PDF's own metadata is only a fallback."

    slug: str
    title: str
    author: str | None = None
    published: str | None = None
    synopsis: str | None = None
    source_sha: str = ""

    def body(self, *, pages: int, ocr_pages: int) -> str:
        lines = [f"[book] {self.title}"]
        if self.author:
            lines.append(f"author: {self.author}")
        if self.published:
            lines.append(f"published: {self.published}")
        lines.append(f"pages: {pages}" + (f" · {ocr_pages} needed OCR" if ocr_pages else ""))
        if self.synopsis:
            lines.append("")
            lines.append(self.synopsis.strip())
        lines.append("")
        lines.append(
            "Reference material. Explicit links associate this book with stored memories.")
        return "\n".join(lines)


@dataclass
class BookProgress:
    slug: str
    title: str = ""
    hub_lineage: str = ""
    total_pages: int = 0
    done_pages: int = 0
    skipped_pages: int = 0
    chunks: int = 0
    ocr_pages: int = 0
    current_page: int = 0
    phase: str = "queued"
    finished: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "book": self.slug, "title": self.title, "hub": self.hub_lineage,
            "total_pages": self.total_pages, "done_pages": self.done_pages,
            "skipped_unchanged": self.skipped_pages, "chunks": self.chunks,
            "ocr_pages": self.ocr_pages, "current_page": self.current_page,
            "phase": self.phase, "finished": self.finished, "error": self.error,
        }


class BookLedger:
    "`book · page` → the chunks we wrote for it, plus the page's sha."

    def __init__(self, path: str) -> None:
        self._db = StateDB(path)
        self._db._con.execute(
            "CREATE TABLE IF NOT EXISTS book_pages ("
            " book TEXT NOT NULL, page INTEGER NOT NULL, sha TEXT NOT NULL,"
            " chunk_lineages TEXT NOT NULL DEFAULT '[]',"
            " PRIMARY KEY (book, page))"
        )
        self._db._con.execute(
            "CREATE TABLE IF NOT EXISTS books ("
            " book TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',"
            " author TEXT NOT NULL DEFAULT '', hub_lineage TEXT NOT NULL,"
            " pages INTEGER NOT NULL DEFAULT 0, source_sha TEXT NOT NULL DEFAULT '')"
        )
        self._db._con.commit()

    def close(self) -> None:
        self._db.close()


    def get_book(self, slug: str) -> dict | None:
        row = self._db._con.execute(
            "SELECT title, author, hub_lineage, pages, source_sha FROM books"
            " WHERE book=?", (slug,)).fetchone()
        if row is None:
            return None
        return {"title": row[0], "author": row[1], "hub_lineage": row[2],
                "pages": row[3], "source_sha": row[4]}

    def put_book(self, slug: str, *, title: str, author: str, hub_lineage: str,
                 pages: int, source_sha: str) -> None:
        self._db._con.execute(
            "INSERT INTO books(book, title, author, hub_lineage, pages, source_sha)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT(book) DO UPDATE SET"
            " title=excluded.title, author=excluded.author,"
            " hub_lineage=excluded.hub_lineage, pages=excluded.pages,"
            " source_sha=excluded.source_sha",
            (slug, title, author, hub_lineage, pages, source_sha))
        self._db._con.commit()

    def books(self) -> list[dict]:
        return [{"book": r[0], "title": r[1], "author": r[2], "hub_lineage": r[3],
                 "pages": r[4]}
                for r in self._db._con.execute(
                    "SELECT book, title, author, hub_lineage, pages FROM books"
                    " ORDER BY title")]


    def get_page(self, slug: str, page: int) -> dict | None:
        row = self._db._con.execute(
            "SELECT sha, chunk_lineages FROM book_pages WHERE book=? AND page=?",
            (slug, page)).fetchone()
        return None if row is None else {"sha": row[0],
                                         "chunk_lineages": json.loads(row[1])}

    def put_page(self, slug: str, page: int, *, sha: str,
                 chunk_lineages: list[str]) -> None:
        self._db._con.execute(
            "INSERT INTO book_pages(book, page, sha, chunk_lineages)"
            " VALUES (?,?,?,?) ON CONFLICT(book, page) DO UPDATE SET"
            " sha=excluded.sha, chunk_lineages=excluded.chunk_lineages",
            (slug, page, sha, json.dumps(chunk_lineages)))
        self._db._con.commit()

    def pages_done(self, slug: str) -> int:
        return int(self._db._con.execute(
            "SELECT COUNT(*) FROM book_pages WHERE book=?", (slug,)).fetchone()[0])


def page_sha(page: BookPage) -> str:
    import hashlib

    return hashlib.sha256(page.text.encode()).hexdigest()[:32]


class BookWriter:
    'Mints the book hub and its page chunks. Idempotent, resumable.'

    def __init__(self, writer: HarnessWriter, ledger: BookLedger) -> None:
        self.writer = writer
        self.ledger = ledger
        self.scope = LIBRARY_SCOPE

    def _entity(self, meta: BookMeta) -> str:
        return self.writer.register_entity(
            "collection", f"book: {meta.title}", f"book-{meta.slug}",
            f"book: {meta.title}" + (f" — {meta.author}" if meta.author else ""),
            aliases=[meta.slug],
        )

    def write_hub(self, meta: BookMeta, extract: BookExtract,
                  progress: BookProgress) -> str:
        prior = self.ledger.get_book(meta.slug)
        hub = self.writer.write_parent(
            body=meta.body(pages=extract.page_count, ocr_pages=extract.ocr_pages),
            entity_id=self._entity(meta),
            scope=self.scope,
            tags=book_tags(meta.slug, author=meta.author),
            memory_id=prior["hub_lineage"] if prior else None,
            learning_eligible=False,
        )
        self.ledger.put_book(meta.slug, title=meta.title, author=meta.author or "",
                             hub_lineage=hub, pages=extract.page_count,
                             source_sha=meta.source_sha)
        progress.hub_lineage = hub
        progress.title = meta.title
        return hub

    def write_page(self, meta: BookMeta, page: BookPage, hub: str,
                   progress: BookProgress) -> list[str]:
        'One page → its chunk(s), `part_of` the hub. Unchanged pages skip.'
        sha = page_sha(page)
        prior = self.ledger.get_page(meta.slug, page.number)
        if prior and prior["sha"] == sha:
            progress.skipped_pages += 1
            return list(prior["chunk_lineages"])
        if not page.text.strip():
            self.ledger.put_page(meta.slug, page.number, sha=sha, chunk_lineages=[])
            return []

        lineages: list[str] = []
        for i, body in enumerate(chunk_text(page.text, chunk_tokens=BOOK_CHUNK_TOKENS)):
            lineages.append(self.writer.write_chunk(
                body=body,
                entity_id=self._entity(meta),
                scope=self.scope,
                parent_lineage=hub,
                index=page.number * 1000 + i,
                extra_tags=book_chunk_tags(meta.slug, page.number),
                learning_eligible=False,
            ))
        self.ledger.put_page(meta.slug, page.number, sha=sha, chunk_lineages=lineages)
        progress.chunks += len(lineages)
        return lineages

    def write(self, meta: BookMeta, extract: BookExtract,
              progress: BookProgress | None = None) -> BookProgress:
        p = progress or BookProgress(slug=meta.slug)
        p.slug = meta.slug
        p.total_pages = extract.page_count
        p.ocr_pages = extract.ocr_pages
        p.phase = "writing"
        hub = self.write_hub(meta, extract, p)
        for page in extract.pages:
            p.current_page = page.number
            self.write_page(meta, page, hub, p)
            p.done_pages += 1
        p.phase = "done"
        p.finished = True
        log.info("book %s: %d pages, %d chunks, %d skipped, hub %s",
                 meta.slug, p.done_pages, p.chunks, p.skipped_pages, hub[:13])
        return p
