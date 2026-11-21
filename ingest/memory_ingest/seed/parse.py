from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from memory_contract import MEMORY_TYPES, SUBJECT_CLASSES

if TYPE_CHECKING:
    from memory_ingest.seed.emotions import EmotionMatcher

_TITLE_RE = re.compile(r"^\*\*\s*(\d+)\.\s*(.+?)\s*\*\*\s*$")
_FOOTER_RE = re.compile(r"^\s*[>*]?\s*\*(?!\*)(.+?)\*\s*$")
_REVISES_RE = re.compile(r"revises\s*:\s*([0-9a-fA-F-]{8,})", re.IGNORECASE)
_BEAT_RE = re.compile(r"^beat\s*:\s*(.+)$", re.IGNORECASE)
_ABOUT_RE = re.compile(r"^\[(.*)\]$")
_TUNE_RE = re.compile(r"\s*\([^)]*\)\s*$")

SEED_TYPES = ("fact", "preference", "episode", "note", "procedure")


@dataclass
class SeedRecord:
    n: int
    title: str
    body: str
    type: str
    subject_class: str
    emotion_name: str
    about: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    beat: str | None = None
    anchor: bool = False
    revises: str | None = None
    category: str | None = None
    item: str | None = None
    emotion_resolved: str | None = None
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def content_hash(self) -> str:
        payload = "␟".join([
            self.title, self.body, self.type, self.subject_class,
            self.emotion_resolved or self.emotion_name,
            ",".join(self.about), ",".join(self.tags),
            self.beat or "", self.category or "", self.item or "",
            "anchor" if self.anchor else "",
        ])
        return hashlib.sha256(payload.encode()).hexdigest()


def _split_footer(raw: str) -> list[str]:
    return [part.strip() for part in raw.split("·") if part.strip()]


def parse_memories(markdown: str) -> list[SeedRecord]:
    records: list[SeedRecord] = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        m = _TITLE_RE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        n, title = int(m.group(1)), m.group(2).strip().rstrip(".")
        body_lines: list[str] = []
        footer: str | None = None
        revises: str | None = None
        i += 1
        while i < len(lines):
            line = lines[i].strip()
            if _TITLE_RE.match(line):
                break
            rv = _REVISES_RE.search(line)
            if rv and not _FOOTER_RE.match(line):
                revises = rv.group(1)
                i += 1
                continue
            f = _FOOTER_RE.match(line)
            if f:
                footer = f.group(1)
                i += 1
                break
            if line:
                body_lines.append(line.lstrip("> ").strip())
            i += 1

        body = " ".join(body_lines).strip()
        rec = SeedRecord(
            n=n, title=title, body=body, type="", subject_class="",
            emotion_name="", revises=revises,
        )
        if footer is None:
            rec.problems.append("missing the italic footer line")
            records.append(rec)
            continue
        if rv2 := _REVISES_RE.search(footer):
            rec.revises = rec.revises or rv2.group(1)

        parts = _split_footer(footer)
        if len(parts) < 3:
            rec.problems.append(f"footer needs at least type · subjectClass · emotion (got {footer!r})")
            records.append(rec)
            continue
        rec.type = parts[0].lower()
        rec.subject_class = parts[1].lower()
        rec.emotion_name = _TUNE_RE.sub("", parts[2]).strip()

        for part in parts[3:]:
            if (b := _BEAT_RE.match(part)):
                rec.beat = b.group(1).strip().lower()
            elif part.lower().startswith("category:"):
                rec.category = part.split(":", 1)[1].strip().lower()
            elif part.lower().startswith("item:"):
                rec.item = part.split(":", 1)[1].strip().lower()
            elif part.upper() == "ANCHOR":
                rec.anchor = True
            elif (a := _ABOUT_RE.match(part)):
                rec.about = [x.strip().lower() for x in a.group(1).split(",") if x.strip()]
            elif _REVISES_RE.match(part):
                continue
            else:
                rec.tags = [x.strip().lower() for x in part.split(",") if x.strip()]
        records.append(rec)
    return records


def validate(rec: SeedRecord) -> None:
    'Fill `rec.problems`. Everything reported, nothing guessed.'
    if rec.type not in SEED_TYPES:
        rec.problems.append(f"type {rec.type!r} not in {'|'.join(SEED_TYPES)}")
    elif rec.type not in MEMORY_TYPES:
        rec.problems.append(f"type {rec.type!r} unknown to the contract")
    if rec.subject_class not in SUBJECT_CLASSES:
        rec.problems.append(
            f"subjectClass {rec.subject_class!r} not in {'|'.join(SUBJECT_CLASSES)}"
        )
    if not rec.body:
        rec.problems.append("empty body")
    elif rec.type in ("episode", "note") and len(re.findall(r"[.!?](\s|$)", rec.body)) < 2:
        rec.problems.append("body is a one-liner (expected 3–6 sentences)")
    if rec.type == "preference":
        if not rec.category:
            rec.problems.append("preference without category: — it has no type")
        if not rec.item:
            rec.problems.append(
                "preference without item: — a second like in this category "
                "would overwrite it")


def _exact_emotion(rec: SeedRecord, by_name: dict[str, dict]) -> bool:
    if not rec.emotion_name:
        rec.problems.append("no emotion given")
        return False
    entry = by_name.get(rec.emotion_name.strip().lower())
    if entry is None:
        return False
    rec.emotion_resolved = str(entry["name"])
    return True


def _typo_emotion(rec: SeedRecord, by_name: dict[str, dict]) -> bool:
    'The last-resort tier: nearest by *spelling*.'
    wanted = rec.emotion_name.strip().lower()
    close = difflib.get_close_matches(wanted, list(by_name), n=2, cutoff=0.72)
    unambiguous = len(close) == 1 or (
        len(close) == 2
        and difflib.SequenceMatcher(None, wanted, close[0]).ratio()
        - difflib.SequenceMatcher(None, wanted, close[1]).ratio() > 0.08
    )
    if not (close and unambiguous):
        return False
    rec.emotion_resolved = str(by_name[close[0]]["name"])
    rec.problems.append(
        f"emotion {rec.emotion_name!r} matched to {rec.emotion_resolved!r} — check it"
    )
    return True


def _refuse_emotion(rec: SeedRecord) -> None:
    rec.problems.append(
        f"emotion {rec.emotion_name!r} is not in the 127-catalog and has no single near match"
    )


def resolve_emotion(
    rec: SeedRecord, catalog: list[dict], matcher: "EmotionMatcher | None" = None
) -> None:
    'Match the written emotion to a real catalog entry.'
    by_name = {str(e["name"]).lower(): e for e in catalog}
    if _exact_emotion(rec, by_name) or not rec.emotion_name:
        return
    if matcher is not None:
        hit = matcher.nearest(rec.emotion_name.strip())
        if hit is not None:
            rec.emotion_resolved = hit.name
            rec.notes.append(
                f"emotion {rec.emotion_name!r} -> {hit.name!r} "
                f"({hit.method} {hit.score:.2f})"
            )
            return
    if not _typo_emotion(rec, by_name):
        _refuse_emotion(rec)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")


def parse_and_validate(
    markdown: str, catalog: list[dict], matcher: "EmotionMatcher | None" = None
) -> list[SeedRecord]:
    'Parse, validate, and resolve every emotion to the catalog.'
    records = parse_memories(markdown)
    by_name = {str(e["name"]).lower(): e for e in catalog}
    for rec in records:
        if not rec.problems:
            validate(rec)
        _exact_emotion(rec, by_name)

    strays = [
        rec.emotion_name.strip()
        for rec in records
        if rec.emotion_name and not rec.emotion_resolved
    ]
    resolved = matcher.resolve_many(strays) if (matcher and strays) else {}

    for rec in records:
        if not rec.emotion_name or rec.emotion_resolved:
            continue
        hit = resolved.get(rec.emotion_name.strip())
        if hit is not None:
            rec.emotion_resolved = hit.name
            rec.notes.append(
                f"emotion {rec.emotion_name!r} -> {hit.name!r} "
                f"({hit.method} {hit.score:.2f})"
            )
        elif not _typo_emotion(rec, by_name):
            _refuse_emotion(rec)
    return records


def substitutions(records: Sequence[SeedRecord]) -> list[tuple[SeedRecord, str]]:
    'Every emotion that was replaced, for the report the operator reviews.'
    return [
        (rec, note)
        for rec in records
        for note in rec.notes
        if note.startswith("emotion ")
    ]
