'The RSS feed → the list of posts that exist.'

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

log = logging.getLogger("memory_ingest.blog.feed")



class FeedRefused(ValueError):
    'The feed is not something we can read.'


@dataclass
class FeedItem:
    guid: str
    title: str
    url: str
    published_at: str
    summary: str = ""
    category: str = ""

    def as_payload(self) -> dict[str, str]:
        out = {
            "guid": self.guid,
            "title": self.title,
            "url": self.url,
            "published_at": self.published_at,
        }
        if self.summary:
            out["summary"] = self.summary
        if self.category:
            out["category"] = self.category
        return out


def _text(node: ElementTree.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _iso(raw: str) -> str:
    'RFC-2822 (`Sun, 27 Sep 2026 21:00:25 GMT`) → ISO-8601 UTC.'
    raw = raw.strip()
    if not raw:
        raise FeedRefused("item has no pubDate")
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError) as exc:
        raise FeedRefused(f"unparseable pubDate: {raw!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_feed(data: bytes) -> list[FeedItem]:
    'Every `<item>` in the channel, newest first as the feed orders them.'
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise FeedRefused(f"feed is not valid XML: {exc}") from exc

    items: list[FeedItem] = []
    for node in root.iterfind(".//channel/item"):
        guid = _text(node.find("guid"))
        link = _text(node.find("link"))
        title = _text(node.find("title"))
        try:
            published = _iso(_text(node.find("pubDate")))
        except FeedRefused as exc:
            log.warning("feed item %r skipped: %s", title or guid, exc)
            continue
        if not (guid and link and title):
            log.warning("feed item skipped: missing guid, link or title (%r)", title)
            continue
        items.append(FeedItem(
            guid=guid, title=title, url=link, published_at=published,
            summary=_text(node.find("description")),
            category=_text(node.find("category")),
        ))
    return items


def missing_from(items: list[FeedItem], known_guids: set[str]) -> list[FeedItem]:
    'Feed items the ledger has never seen, oldest first.'
    return sorted(
        (i for i in items if i.guid not in known_guids),
        key=lambda i: i.published_at,
    )
