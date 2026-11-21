from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from memory_ingest.seed.hub import HubWriter
from memory_ingest.seed.parse import SeedRecord, validate
from memory_ingest.seed.writer import (
    MemoryWriter,
    SeedLedger,
    SeedOutcome,
    plan_and_write,
)

log = logging.getLogger("memory_ingest.seed.service")


class NotApproved(RuntimeError):
    'The submission did not assert human approval.'


@dataclass
class SeedResult:
    chapter: str
    written: list[dict] = field(default_factory=list)
    refused: list[dict] = field(default_factory=list)
    substitutions: list[str] = field(default_factory=list)
    hub_lineage: str = ""
    hub_attached: int = 0
    beat_links: int = 0
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "chapter": self.chapter,
            "written": self.written,
            "refused": self.refused,
            "emotion_substitutions": self.substitutions,
            "hub": self.hub_lineage,
            "hub_attached": self.hub_attached,
            "beat_links": self.beat_links,
            "dry_run": self.dry_run,
            "counts": {
                "submitted": len(self.written) + len(self.refused),
                "written": len(self.written),
                "refused": len(self.refused),
            },
        }


def record_from_payload(item: dict[str, Any], index: int) -> SeedRecord:
    'One JSON record → a `SeedRecord`. Shape only; validation comes after.'
    return SeedRecord(
        n=int(item.get("n") or index + 1),
        title=str(item.get("title") or "").strip(),
        body=str(item.get("body") or item.get("summary") or "").strip(),
        type=str(item.get("type") or "").strip().lower(),
        subject_class=str(item.get("subject_class") or item.get("subjectClass")
                          or "").strip().lower(),
        emotion_name=str(item.get("emotion") or item.get("emotion_name") or "").strip(),
        about=[str(a).strip().lower() for a in (item.get("about") or []) if str(a).strip()],
        tags=[str(t).strip().lower() for t in (item.get("tags") or []) if str(t).strip()],
        beat=(str(item["beat"]).strip().lower() or None) if item.get("beat") else None,
        anchor=bool(item.get("anchor")),
        revises=str(item["revises"]) if item.get("revises") else None,
        category=(str(item["category"]).strip().lower() if item.get("category") else None),
        item=(str(item["item"]).strip().lower() if item.get("item") else None),
    )


def resolve_emotions(records: list[SeedRecord], catalog: list[dict],
                     matcher: Any | None) -> list[str]:
    'Exact → semantic snap. Returns the substitutions made, for the report.'
    by_name = {str(e["name"]).lower(): str(e["name"]) for e in catalog}
    subs: list[str] = []
    strays: list[str] = []
    for rec in records:
        exact = by_name.get(rec.emotion_name.strip().lower())
        if exact:
            rec.emotion_resolved = exact
        elif rec.emotion_name:
            strays.append(rec.emotion_name.strip())

    if strays and matcher is not None:
        resolved = matcher.resolve_many(sorted(set(strays)))
        for rec in records:
            if rec.emotion_resolved or not rec.emotion_name:
                continue
            hit = resolved.get(rec.emotion_name.strip())
            if hit is None:
                continue
            rec.emotion_resolved = hit.name
            note = (f"#{rec.n} {rec.title}: {rec.emotion_name!r} -> "
                    f"{hit.name!r} ({hit.method} {hit.score:.2f})")
            rec.notes.append(note)
            subs.append(note)

    for rec in records:
        if rec.emotion_name and not rec.emotion_resolved:
            rec.problems.append(
                f"emotion {rec.emotion_name!r} is not in the 127-catalog and "
                "could not be resolved")
    return subs


def link_beats(records: list[SeedRecord], outcomes: list[SeedOutcome],
               writer: MemoryWriter) -> int:
    'Join same-beat memories: facts/preferences point at their episode.'
    by_n = {o.n: o.lineage_id for o in outcomes if o.lineage_id}
    beats: dict[str, list[SeedRecord]] = {}
    for rec in records:
        if rec.beat and rec.ok and rec.n in by_n:
            beats.setdefault(rec.beat, []).append(rec)

    made = 0
    for members in beats.values():
        if len(members) < 2:
            continue
        episodes = [m for m in members if m.type == "episode"]
        target = (episodes or members)[0]
        for m in members:
            if m is target:
                continue
            try:
                if writer.link_once(by_n[m.n], by_n[target.n], "relates_to"):
                    made += 1
            except Exception as exc:  # noqa: BLE001 - one edge, not the batch
                log.warning("beat link failed for #%s: %s", m.n, exc)
    return made


def ingest_records(
    payload: dict[str, Any],
    *,
    writer: MemoryWriter,
    ledger: SeedLedger,
    hub_writer: HubWriter,
    catalog: list[dict],
    matcher: Any | None = None,
    existing_hub: str | None = None,
) -> SeedResult:
    'The whole path, in order. Raises `NotApproved` without the flag.'
    if not payload.get("approved"):
        raise NotApproved(
            "refused: seeded memories become her identity and her training "
            "data. Submit approved=true only after a human has reviewed them.")

    chapter = str(payload.get("chapter") or "").strip()
    if not chapter:
        raise ValueError("chapter is required — it is the ledger and hub identity")

    dry_run = bool(payload.get("dry_run"))
    raw: Sequence[dict] = payload.get("records") or []
    records = [record_from_payload(item, i) for i, item in enumerate(raw)]

    for rec in records:
        validate(rec)
    subs = resolve_emotions(records, catalog, matcher)

    result = SeedResult(chapter=chapter, substitutions=subs, dry_run=dry_run)
    result.refused = [{"n": r.n, "title": r.title, "problems": r.problems}
                      for r in records if not r.ok]

    outcomes = plan_and_write(records, chapter=chapter,
                              writer=None if dry_run else writer,
                              ledger=ledger, dry_run=dry_run)
    result.written = [{"n": o.n, "title": o.title, "action": o.action,
                       "lineage_id": o.lineage_id, "revision": o.revision}
                      for o in outcomes]
    if dry_run:
        return result

    lineages = [o.lineage_id for o in outcomes if o.lineage_id]
    if lineages:
        counts: dict[str, int] = {}
        for rec in records:
            if rec.ok:
                counts[rec.type] = counts.get(rec.type, 0) + 1
        report = hub_writer.attach_batch(chapter, lineages, counts=counts,
                                         existing_hub=existing_hub)
        result.hub_lineage = report.hub_lineage
        result.hub_attached = report.attached

    result.beat_links = link_beats(records, outcomes, writer)
    log.info("chapter %s: %d written, %d refused, hub %s (+%d), %d beat links",
             chapter, len(result.written), len(result.refused),
             result.hub_lineage[:13], result.hub_attached, result.beat_links)
    return result
