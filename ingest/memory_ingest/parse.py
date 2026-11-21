from __future__ import annotations

from dataclasses import dataclass

from bs4 import BeautifulSoup

_DROP_TAGS = ("script", "style", "noscript", "template", "svg", "iframe",
              "nav", "header", "footer", "aside", "form", "button")

THIN_CHARS = 500


@dataclass
class ParsedHtml:
    title: str
    text: str

    @property
    def thin(self) -> bool:
        return len(self.text.strip()) < THIN_CHARS


def parse_html(data: bytes) -> ParsedHtml:
    soup = BeautifulSoup(data, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "") or "untitled"
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    blocks: list[str] = []
    for para in root.find_all(("p", "h1", "h2", "h3", "h4", "li", "pre", "td", "th")):
        text = para.get_text(" ", strip=True)
        if text:
            blocks.append(text)
    if not blocks:
        text = root.get_text("\n", strip=True)
        return ParsedHtml(title=title, text=text)
    return ParsedHtml(title=title, text="\n\n".join(blocks))
