'One blog post, normalised.'

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import os

_ALIASES = {
    "publishedAt": "published_at", "updatedAt": "updated_at",
    "bodyMarkdown": "body_markdown", "bodyText": "body_text",
    "readingMinutes": "reading_minutes", "canonicalUrl": "url",
    "datePublished": "published_at", "dateModified": "updated_at",
    "headline": "title", "description": "summary",
}

_GUID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class BlogRefused(ValueError):
    'The payload is not something we will accept.'


@dataclass
class BlogPost:
    guid: str
    title: str
    url: str
    published_at: str
    updated_at: str = ""
    slug: str = ""
    summary: str = ""
    category: str = ""
    topics: list[str] = field(default_factory=list)
    author: str = ""
    body: str = ""
    image: str = ""
    reading_minutes: int = 0

    @property
    def date(self) -> str:
        return _as_date(self.published_at)

    def hub_body(self) -> str:
        lines = [f"[blog] {self.title}"]
        if self.summary:
            lines.extend(["", self.summary])
        meta = [self.url]
        if self.category:
            meta.append(self.category)
        if self.reading_minutes:
            meta.append(f"{self.reading_minutes} min read")
        lines.extend(["", " · ".join(meta)])
        return "\n".join(lines)

    def artifact_body(self) -> str:
        "The neutral half's header. The prose itself lives in the chunks."
        lines = [self.title, self.url, f"published {self.published_at}"]
        if self.author:
            lines.append(f"by {self.author}")
        return "\n".join(lines)

    def content_hash(self) -> str:
        'Everything that would change what we stored.'
        material = "␟".join([
            self.title, self.url, self.summary, self.category,
            ",".join(self.topics), self.author, self.body,
            self.image, str(self.reading_minutes),
        ])
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _as_date(value: str) -> str:
    'ISO-8601 → `YYYY-MM-DD` in UTC.'
    text = (value or "").strip()
    if not text:
        raise BlogRefused("published_at is required")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BlogRefused(f"published_at is not ISO-8601: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date().isoformat()


def _normalise(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    for camel, snake in _ALIASES.items():
        if camel in out and snake not in out:
            out[snake] = out.pop(camel)
    return out


def _clean_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def parse_blog(payload: dict[str, Any]) -> BlogPost:
    'Validate and normalise. Raises `BlogRefused` with a usable message.'
    if not isinstance(payload, dict):
        raise BlogRefused("payload must be a JSON object")
    data = _normalise(payload)

    guid = _clean_str(data.get("guid"))
    if not guid:
        raise BlogRefused("guid is required — it is the post's identity")
    if not _GUID_RE.match(guid):
        raise BlogRefused(f"guid {guid!r} is not a usable identifier")

    title = _clean_str(data.get("title"))
    if not title:
        raise BlogRefused("title is required")

    url = _clean_str(data.get("url"))
    if not url:
        raise BlogRefused("url is required")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {h.strip().lower() for h in os.environ.get("BLOG_ALLOWED_HOSTS", "").split(",") if h.strip()}:
        raise BlogRefused(
            f"url must be https on a host configured in BLOG_ALLOWED_HOSTS, got {url!r}"
        )

    published_at = _clean_str(data.get("published_at"))
    date = _as_date(published_at)

    body = _clean_str(data.get("body_markdown")) or _clean_str(data.get("body_text"))

    topics_raw = data.get("topics") or data.get("tags") or []
    if isinstance(topics_raw, str):
        topics_raw = [topics_raw]
    topics = [t for t in (_clean_str(x) for x in topics_raw) if t]

    reading = data.get("reading_minutes") or 0
    try:
        reading_minutes = max(0, int(reading))
    except (TypeError, ValueError):
        reading_minutes = 0

    post = BlogPost(
        guid=guid,
        title=title,
        url=url,
        published_at=published_at,
        updated_at=_clean_str(data.get("updated_at")) or published_at,
        slug=_clean_str(data.get("slug")) or _slug_from_url(url),
        summary=_clean_str(data.get("summary")),
        category=_clean_str(data.get("category")),
        topics=topics,
        author=_clean_str(data.get("author")) or "",
        body=body,
        image=_clean_str(data.get("image")),
        reading_minutes=reading_minutes,
    )
    assert post.date == date
    return post


def _slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return path.rsplit("/", 1)[-1] if path else ""
