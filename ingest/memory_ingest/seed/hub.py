'The episodic-memory-hub: the node that makes sure nothing floats.'

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from memory_contract import PART_OF_LINK, episodic_hub_tags

log = logging.getLogger("memory_ingest.seed.hub")


@dataclass
class HubReport:

    chapter: str
    hub_lineage: str = ""
    minted_hub: bool = False
    attached: int = 0
    already_attached: int = 0
    failed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "chapter": self.chapter, "hub": self.hub_lineage,
            "minted_hub": self.minted_hub, "attached": self.attached,
            "already_attached": self.already_attached, "failed": self.failed,
        }


def hub_body(chapter: str, *, title: str | None = None,
             counts: dict[str, int] | None = None,
             source: str | None = None) -> str:
    lines = [f"[episodic-memory-hub] Chapter {chapter}"]
    if title:
        lines.append(f"title: {title}")
    if source:
        lines.append(f"source: {source}")
    if counts:
        lines.append("memories: " + " · ".join(
            f"{k}: {v}" for k, v in sorted(counts.items()) if v))
    lines.append(
        "Every memory seeded from this chapter is `part_of` this node, so the "
        "chapter can be walked as one unit and no memory is left unreachable.")
    return "\n".join(lines)


class HubWriter:
    "Mints a chapter's hub and attaches memories to it. Idempotent."

    def __init__(self, writer: object, scope: str = "reference") -> None:
        self.writer = writer
        self.scope = scope

    def entity_for(self, chapter: str) -> str:
        slug = f"chapter-{chapter}"
        return self.writer.register_entity(  # type: ignore[attr-defined]
            "collection", f"Chapter {chapter}", slug,
            f"episodic memory hub: chapter {chapter}", aliases=[slug],
        )

    def ensure_hub(self, chapter: str, *, title: str | None = None,
                   counts: dict[str, int] | None = None,
                   source: str | None = None,
                   existing: str | None = None) -> tuple[str, bool]:
        "The chapter's hub lineage, minting it only if `existing` is None."
        entity = self.entity_for(chapter)
        lineage = self.writer.write_parent(  # type: ignore[attr-defined]
            body=hub_body(chapter, title=title, counts=counts, source=source),
            entity_id=entity,
            scope=self.scope,
            tags=episodic_hub_tags(chapter, title=title),
            memory_id=existing,
        )
        return lineage, existing is None

    def attach(self, hub_lineage: str, memory_lineages: list[str],
               report: HubReport) -> HubReport:
        '`part_of` from every memory to the hub. Existing edges are skipped.'
        for lineage in memory_lineages:
            if lineage == hub_lineage:
                continue
            try:
                made = self.writer.link_once(  # type: ignore[attr-defined]
                    lineage, hub_lineage, PART_OF_LINK)
            except Exception as exc:  # noqa: BLE001 - see docstring
                log.warning("could not attach %s: %s", lineage[:13], exc)
                report.failed.append(lineage)
                continue
            if made:
                report.attached += 1
            else:
                report.already_attached += 1
        return report

    def attach_batch(self, chapter: str, memory_lineages: list[str], *,
                     title: str | None = None,
                     counts: dict[str, int] | None = None,
                     source: str | None = None,
                     existing_hub: str | None = None) -> HubReport:
        'Mint-or-revise the hub, then attach the whole batch to it.'
        report = HubReport(chapter=chapter)
        hub, minted = self.ensure_hub(chapter, title=title, counts=counts,
                                      source=source, existing=existing_hub)
        report.hub_lineage, report.minted_hub = hub, minted
        self.attach(hub, memory_lineages, report)
        log.info("chapter %s → hub %s: %d attached, %d already, %d failed",
                 chapter, hub[:13], report.attached, report.already_attached,
                 len(report.failed))
        return report
