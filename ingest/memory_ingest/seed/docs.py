from __future__ import annotations

from pathlib import Path

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst"}


class UnreadableChapter(ValueError):
    pass


def load_chapter(path: str | Path) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        raise UnreadableChapter(f"not a file: {p}")
    suffix = p.suffix.lower()

    if suffix in TEXT_SUFFIXES:
        return p.read_text(encoding="utf-8", errors="replace").strip()

    if suffix == ".docx":
        try:
            import docx
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise UnreadableChapter(
                "reading .docx needs python-docx (pip install python-docx)"
            ) from exc
        try:
            document = docx.Document(str(p))
        except Exception as exc:  # noqa: BLE001 — surface the real reason
            raise UnreadableChapter(f"cannot open {p.name}: {exc}") from exc
        paragraphs = [para.text.strip() for para in document.paragraphs]
        text = "\n\n".join(t for t in paragraphs if t)
        if not text.strip():
            raise UnreadableChapter(f"{p.name} has no readable text")
        return text

    raise UnreadableChapter(
        f"unsupported chapter format: {suffix or p.name} (use .docx, .txt or .md)"
    )


def render_emotion_catalog(catalog: list[dict]) -> str:
    'The 127, grouped by family, for `{{EMOTION_CATALOG}}`.'
    by_family: dict[str, list[str]] = {}
    for e in catalog:
        by_family.setdefault(str(e.get("family", "")), []).append(str(e["name"]))
    lines = ["THE CATALOG — the ONLY permitted emotion names:"]
    for family in sorted(by_family):
        lines.append(f"  {family}: {', '.join(sorted(by_family[family]))}")
    return "\n".join(lines)


def render_prompt(template: str, *, label: str, chapter_text: str, **extra: str) -> str:
    out = template.replace("{{CHAPTER_LABEL}}", label)
    out = out.replace("{{CHAPTER_TEXT}}", chapter_text)
    for key, value in extra.items():
        out = out.replace("{{" + key + "}}", value)
    return out
