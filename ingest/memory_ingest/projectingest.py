from __future__ import annotations

import logging
from pathlib import Path

from memory_contract import DOCUMENT_TAG, PART_OF_LINK

from memory_ingest.config import Settings
from memory_ingest.pipeline import Ingestor
from memory_ingest.project import DEFAULT_IGNORE, Manifest, scan_project
from memory_ingest.projectwriter import (
    ProjectLedger,
    ProjectProgress,
    ProjectWriter,
    _slug,
)

log = logging.getLogger("memory_ingest.projectingest")


def _dir_of(relpath: str) -> str:
    return relpath.rsplit("/", 1)[0] if "/" in relpath else ""


def ingest_project(
    root: str,
    project: str | None,
    *,
    settings: Settings,
    ingestor: Ingestor,
    ledger: ProjectLedger,
    ignore: list[str] | None = None,
    progress: ProjectProgress | None = None,
) -> ProjectProgress:
    'Scan → project root → directories → documents/placeholders.'
    manifest: Manifest = scan_project(
        root, project, ignore=tuple(ignore) if ignore else DEFAULT_IGNORE
    )
    prog = progress or ProjectProgress(project=manifest.project)
    prog.project = manifest.project
    prog.total_files = len(manifest.files)

    pw = ProjectWriter(ingestor.writer, ledger, settings.artifact_scope)
    pw.write_root(manifest, prog)
    dir_lineages = pw.write_dirs(manifest, prog)
    log.info("project %s: %d dirs, %d files", manifest.project,
             len(manifest.dirs), len(manifest.files))

    slug = _slug(manifest.project)
    base = Path(manifest.root)

    for f in manifest.files:
        prog.current = f.relpath
        dir_lineage = dir_lineages.get(_dir_of(f.relpath), prog.root_lineage)

        if f.kind == "placeholder":
            pw.write_placeholder(manifest, f, dir_lineage, prog)
            prog.done_files += 1
            continue

        prior = ledger.get(manifest.project, f.relpath)
        if prior and prior["sha"] == f.sha:
            prog.skipped += 1
            prog.done_files += 1
            continue

        try:
            result = ingestor.ingest(
                str(base / f.relpath),
                kind="document",
                title=f"{manifest.project}: {f.relpath}",
                scope=settings.artifact_scope,
                entity_id=f"collection:{slug}",
            )
        except Exception as exc:  # noqa: BLE001 — one bad file must not kill the run
            log.warning("project %s: %s failed (%s) — recorded as unread",
                        manifest.project, f.relpath, exc)
            pw.write_placeholder(manifest, f, dir_lineage, prog)
            prog.done_files += 1
            continue

        ingestor.writer.link(result.parent_lineage, dir_lineage, PART_OF_LINK)
        ledger.put(manifest.project, f.relpath, kind=DOCUMENT_TAG,
                   lineage_id=result.parent_lineage, sha=f.sha,
                   chunk_lineages=result.chunk_lineages)
        prog.documents += 1
        prog.chunks += len(result.chunk_lineages)
        prog.done_files += 1
        log.info("project %s: %s → %d chunks (%d/%d)", manifest.project,
                 f.relpath, len(result.chunk_lineages), prog.done_files,
                 prog.total_files)

    prog.current = ""
    prog.finished = True
    return prog
