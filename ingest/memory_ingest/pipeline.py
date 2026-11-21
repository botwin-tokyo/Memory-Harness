'The pipeline: source → route → text → chunks → linked neutral artifacts.'

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
import re
from dataclasses import dataclass, field

import memory_contract as contract

from memory_ingest import fetcher
from memory_ingest.chunker import chunk_text
from memory_ingest.config import Settings
from memory_ingest.ocr import OcrClient
from memory_ingest.parse import parse_html
from memory_ingest.pdfpages import rasterize
from memory_ingest.render import render_url_to_pdf
from memory_ingest.state import StateDB
from memory_ingest.writer import HarnessWriter

log = logging.getLogger("memory_ingest.pipeline")


@dataclass
class IngestResult:
    entity_id: str
    parent_lineage: str
    chunk_lineages: list[str] = field(default_factory=list)
    route: str = ""
    deduped: bool = False
    grounded_to: str | None = None


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:64] or "untitled"


class Ingestor:
    def __init__(
        self,
        settings: Settings,
        writer: HarnessWriter,
        ocr: OcrClient,
        state: StateDB,
        renderer=render_url_to_pdf,  # type: ignore[no-untyped-def]
    ) -> None:
        self.settings = settings
        self.writer = writer
        self.ocr = ocr
        self.state = state
        self.renderer = renderer


    def _to_text(
        self, src: fetcher.Source, force_render: bool
    ) -> tuple[str, str, str]:
        '→ (text, route, title).'
        if src.content_type == "text":
            return src.data.decode("utf-8", errors="replace"), "text", src.title_hint
        if src.content_type == "image":
            return self.ocr.ocr_pages([src.data]), "image-ocr", src.title_hint
        if src.content_type == "word":
            import tempfile

            from memory_ingest.seed.docs import load_chapter

            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                tmp.write(src.data)
                tmp_path = tmp.name
            try:
                return load_chapter(tmp_path), "word-docx", src.title_hint
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        if src.content_type == "pdf":
            return self.ocr.ocr_pages(rasterize(src.data)), "pdf-ocr", src.title_hint
        if src.content_type == "html":
            parsed = parse_html(src.data)
            is_url = src.source_ref.startswith(("http://", "https://"))
            if (force_render or parsed.thin) and is_url:
                log.info("html %s → render→pdf→ocr (thin=%s force=%s)",
                         src.source_ref, parsed.thin, force_render)
                pdf = self.renderer(src.source_ref, self.settings)
                return self.ocr.ocr_pages(rasterize(pdf)), "html-render-ocr", parsed.title
            return parsed.text, "html-parse", parsed.title
        raise ValueError(f"unroutable content type: {src.content_type}")


    def ingest(
        self,
        source: str,
        *,
        kind: str = "document",
        title: str | None = None,
        about: str | None = None,
        scope: str | None = None,
        link_to: str | None = None,
        force_render: bool = False,
        entity_id: str | None = None,
    ) -> IngestResult:
        settings = self.settings
        src = fetcher.acquire(source, settings)
        text, route, parsed_title = self._to_text(src, force_render)
        text = text.strip()
        if not text:
            raise ValueError(f"source produced no text ({route}): {source}")
        title = title or parsed_title or src.title_hint
        digest = hashlib.sha256(text.encode()).hexdigest()
        scope = scope or settings.artifact_scope

        prior = self.state.get(src.source_ref)
        if prior and prior["text_sha256"] == digest:
            log.info("dedup: %s unchanged", src.source_ref)
            result = IngestResult(
                entity_id=prior["entity_id"],
                parent_lineage=prior["parent_lineage"],
                chunk_lineages=prior["chunk_lineages"],
                route=route,
                deduped=True,
            )
            if link_to:
                self.writer.ground(link_to, prior["parent_lineage"])
                result.grounded_to = link_to
            return result

        if entity_id is None:
            slug = _slugify(title)
            entity_id = self.writer.register_entity(
                kind, title, slug, f"source: {src.source_ref}",
                aliases=[a for a in (about,) if a],
            )
        chunks = chunk_text(text, settings.chunk_tokens, settings.chunk_overlap_tokens)
        parent_body = (
            f"[artifact] {title}\n"
            f"kind: {kind} · chunks: {len(chunks)} · route: {route}\n"
            f"source: {src.source_ref}\n"
            f"sha256: {digest[:16]}"
        )
        parent_lineage = self.writer.write_parent(
            body=parent_body, entity_id=entity_id, scope=scope,
            tags=[contract.ARTIFACT_TAG, kind, f"src-sha:{digest[:16]}"],
            memory_id=prior["parent_lineage"] if prior else None,
        )

        old_chunks: list[str] = prior["chunk_lineages"] if prior else []
        chunk_lineages: list[str] = []
        for i, body in enumerate(chunks):
            chunk_lineages.append(self.writer.write_chunk(
                body=body, entity_id=entity_id, scope=scope,
                parent_lineage=parent_lineage, index=i,
                memory_id=old_chunks[i] if i < len(old_chunks) else None,
            ))
        for stale in old_chunks[len(chunks):]:
            self.writer.forget(stale, "re-ingest: source has fewer chunks")

        result = IngestResult(
            entity_id=entity_id, parent_lineage=parent_lineage,
            chunk_lineages=chunk_lineages, route=route,
        )
        if link_to:
            self.writer.ground(link_to, parent_lineage)
            result.grounded_to = link_to

        self.state.put(
            src.source_ref, text_sha256=digest, entity_id=entity_id,
            parent_lineage=parent_lineage, chunk_lineages=chunk_lineages,
        )
        log.info("ingested %s: %d chunks via %s → %s",
                 src.source_ref, len(chunk_lineages), route, parent_lineage)
        return result
