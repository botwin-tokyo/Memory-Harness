"Rewriting a chapter's stray emotions in place, visibly."

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from memory_ingest.seed.emotions import EmotionMatcher
from memory_ingest.seed.parse import _TUNE_RE, _FOOTER_RE, _TITLE_RE

log = logging.getLogger("memory_ingest.seed.normalize")

REPORT_HEADING = "## Emotion corrections"
_REPORT_BLOCK = re.compile(
    rf"^{re.escape(REPORT_HEADING)}\n(?:.*\n)*?(?:\n|\Z)", re.MULTILINE
)


@dataclass
class Corrections:

    substitutions: list["Substitution"]
    unresolved: list[str]

    def __bool__(self) -> bool:
        return bool(self.substitutions or self.unresolved)


@dataclass
class Substitution:
    n: int
    title: str
    before: str
    after: str
    score: float
    method: str

    def line(self) -> str:
        return (f"- #{self.n} {self.title}: {self.before} -> {self.after} "
                f"({self.method} {self.score:.2f})")


def _strip_report(text: str) -> str:
    "Remove a previous run's report so re-normalising doesn't stack them."
    return _REPORT_BLOCK.sub("", text, count=1)


def _render_report(subs: list[Substitution]) -> str:
    if not subs:
        body = "- none — every emotion was already a catalog name.\n"
    else:
        body = "".join(f"{s.line()}\n" for s in subs)
    return f"{REPORT_HEADING}\n{body}\n"


def _insert_report(text: str, report: str) -> str:
    lines = text.splitlines(keepends=True)
    at = 0
    for i, line in enumerate(lines):
        if _TITLE_RE.match(line.strip()):
            at = i
            break
        at = i + 1
    while at > 0 and lines[at - 1].strip() == "":
        at -= 1
    head, tail = "".join(lines[:at]), "".join(lines[at:])
    if head and not head.endswith("\n"):
        head += "\n"
    return f"{head}\n{report}{tail}"


def collect_emotions(text: str) -> list[tuple[int, int, str, str]]:
    '`(line index, record number, title, emotion as written)` per footer.'
    out: list[tuple[int, int, str, str]] = []
    n, title = 0, ""
    for i, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if (m := _TITLE_RE.match(line)):
            n, title = int(m.group(1)), m.group(2).strip().rstrip(".")
            continue
        f = _FOOTER_RE.match(line)
        if not f:
            continue
        parts = [p.strip() for p in f.group(1).split("·") if p.strip()]
        if len(parts) < 3:
            continue
        out.append((i, n, title, parts[2]))
    return out


def _rewrite_footer(raw: str, before: str, after: str) -> str:
    f = _FOOTER_RE.match(raw.strip())
    if not f:
        return raw
    parts = f.group(1).split("·")
    for i, part in enumerate(parts):
        if _TUNE_RE.sub("", part).strip() == before:
            tune = part[len(part.rstrip()):]
            lead = part[: len(part) - len(part.lstrip())]
            suffix = _TUNE_RE.search(part)
            parts[i] = lead + after + (suffix.group(0).rstrip() if suffix else "") + tune
            break
    else:
        return raw
    inner = "·".join(parts)
    return raw.replace(f.group(1), inner, 1)


def normalize_emotions(
    text: str, matcher: EmotionMatcher
) -> tuple[str, Corrections]:
    'Return the corrected text, every substitution, and every refusal.'
    original = _strip_report(text)
    found = collect_emotions(original)
    strays = [bare for _, _, _, written in found
              if matcher.exact(bare := _TUNE_RE.sub("", written).strip()) is None]

    resolved = matcher.resolve_many(strays) if strays else {}

    lines = original.splitlines(keepends=True)
    subs: list[Substitution] = []
    unresolved: list[str] = []
    for idx, n, title, written in found:
        bare = _TUNE_RE.sub("", written).strip()
        hit = resolved.get(bare)
        if hit is None:
            if bare in strays and bare not in unresolved:
                unresolved.append(bare)
            continue
        if hit.name == bare:
            continue
        lines[idx] = _rewrite_footer(lines[idx], bare, hit.name)
        subs.append(Substitution(n=n, title=title, before=bare, after=hit.name,
                                 score=hit.score, method=hit.method))

    result = Corrections(substitutions=subs, unresolved=unresolved)
    if not subs:
        return text, result
    return _insert_report("".join(lines), _render_report(subs)), result


def normalize_emotions_in_file(
    path: str | Path, matcher: EmotionMatcher
) -> Corrections:
    p = Path(path).expanduser()
    text = p.read_text(encoding="utf-8")
    fixed, result = normalize_emotions(text, matcher)
    if fixed != text:
        p.write_text(fixed, encoding="utf-8")
    log.info("normalised %s — %d correction(s), %d refused",
             p.name, len(result.substitutions), len(result.unresolved))
    return result
