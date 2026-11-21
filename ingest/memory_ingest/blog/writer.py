'A blog post → its subtree. Idempotent; a re-send is a REVISION.'

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from memory_contract import (
    BLOG_SCOPE,
    NEUTRALITY_EMOTION_NAME,
    SAME_DAY_LINK,
    blog_article_tags,
    blog_chunk_tags,
    blog_lineage_key,
    blog_tags,
)

from memory_ingest.blog.ledger import BlogLedger
from memory_ingest.blog.model import BlogPost
from memory_ingest.chunker import chunk_text
from memory_ingest.writer import HarnessWriter

log = logging.getLogger("memory_ingest.blog.writer")

DEFAULT_EMOTION = NEUTRALITY_EMOTION_NAME


@dataclass
class BlogResult:
    guid: str
    key: str
    date: str
    hub_lineage: str = ""
    artifact_lineage: str = ""
    minted: bool = False
    revised: bool = False
    chunks_written: int = 0
    emotion: str = DEFAULT_EMOTION
    same_day: str | None = None
    grounded: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "guid": self.guid, "key": self.key, "date": self.date,
            "hub": self.hub_lineage, "artifact": self.artifact_lineage,
            "minted": self.minted, "revised": self.revised,
            "chunks_written": self.chunks_written, "emotion": self.emotion,
            "same_day": self.same_day, "grounded": self.grounded,
        }


class BlogWriter:
    "Mints or revises one post's hub, article and body chunks."

    def __init__(
        self,
        writer: HarnessWriter,
        ledger: BlogLedger,
        *,
        artifact_scope: str = "reference",
        emotion_resolver: object = None,
        chunk_tokens: int = 800,
        chunk_overlap_tokens: int = 80,
    ) -> None:
        self.writer = writer
        self.ledger = ledger
        self.artifact_scope = artifact_scope
        self.resolve_emotion = emotion_resolver
        self.chunk_tokens = chunk_tokens
        self.chunk_overlap_tokens = chunk_overlap_tokens


    def _entity(self, post: BlogPost) -> str:
        'One collection entity per POST, so a post is walkable as a unit.'
        slug = f"blog-{post.slug or post.guid}"
        return self.writer.register_entity(
            "collection", post.title, slug,
            f"blog post: {post.title}", aliases=[slug, post.guid],
        )

    def _emotion_for(self, post: BlogPost) -> str:
        text = post.summary or post.title
        if not text or self.resolve_emotion is None:
            return DEFAULT_EMOTION
        try:
            got = self.resolve_emotion(text)  # type: ignore[operator]
        except Exception as exc:  # noqa: BLE001 - a post must land regardless
            log.warning("emotion resolution failed for %s: %s", post.guid, exc)
            return DEFAULT_EMOTION
        return str(got) if got else DEFAULT_EMOTION

    def _pair_with_day(self, post: BlogPost, hub: str, result: BlogResult) -> None:
        "`same_day` to that date's journal hub, if it exists yet."
        journal = self.writer.find_hub_by_date("journal", post.date)
        if not journal:
            log.info("no journal for %s yet; the curator pass will pair it",
                     post.date)
            return
        try:
            if self.writer.link_once(hub, journal, SAME_DAY_LINK):
                result.same_day = post.date
            else:
                result.same_day = post.date
        except Exception as exc:  # noqa: BLE001 - pairing must not fail a post
            log.warning("same_day link for %s refused: %s", post.guid, exc)


    def write(self, post: BlogPost) -> BlogResult:
        key = blog_lineage_key(post.guid)
        prior = self.ledger.get(post.guid)
        digest = post.content_hash()

        result = BlogResult(guid=post.guid, key=key, date=post.date,
                            minted=prior is None)

        if prior and prior.get("content_hash") == digest:
            log.info("blog %s unchanged; no-op", post.guid)
            result.hub_lineage = str(prior["hub_lineage"])
            result.artifact_lineage = str(prior["artifact_lineage"])
            result.chunks_written = 0
            result.grounded = True
            result.same_day = prior.get("same_day") or None
            return result

        result.revised = prior is not None
        result.emotion = self._emotion_for(post)
        entity = self._entity(post)

        artifact = self.writer.write_parent(
            body=post.artifact_body(),
            entity_id=entity,
            scope=self.artifact_scope,
            tags=blog_article_tags(post.guid, date=post.date, slug=post.slug),
            memory_id=prior["artifact_lineage"] if prior else None,
            learning_eligible=False,
            emotion=NEUTRALITY_EMOTION_NAME,
            subject_class="world",
        )
        result.artifact_lineage = artifact

        known: list[str] = list(prior["chunk_lineages"]) if prior else []
        written: list[str] = []
        for index, body in enumerate(
            chunk_text(post.body, self.chunk_tokens, self.chunk_overlap_tokens)
        ):
            lineage = self.writer.write_chunk(
                body=body,
                entity_id=entity,
                scope=self.artifact_scope,
                parent_lineage=artifact,
                index=index,
                memory_id=known[index] if index < len(known) else None,
                extra_tags=blog_chunk_tags(post.guid, index),
                learning_eligible=False,
            )
            written.append(lineage)
            result.chunks_written += 1

        hub = self.writer.write_parent(
            body=post.hub_body(),
            entity_id=entity,
            scope=BLOG_SCOPE,
            tags=blog_tags(post.guid, date=post.date, slug=post.slug,
                           category=post.category, topics=post.topics),
            memory_id=prior["hub_lineage"] if prior else None,
            emotion=result.emotion,
            subject_class="self",
        )
        result.hub_lineage = hub

        if prior is None:
            self.writer.ground(hub, artifact)
        result.grounded = True

        self._pair_with_day(post, hub, result)

        self.ledger.put(
            post.guid,
            date=post.date,
            url=post.url,
            title=post.title,
            hub_lineage=hub,
            artifact_lineage=artifact,
            chunk_lineages=written,
            content_hash=digest,
            updated_at=post.updated_at,
            same_day=result.same_day or "",
        )
        return result
