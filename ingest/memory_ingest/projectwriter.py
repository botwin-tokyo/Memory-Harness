'A manifest → a project subtree in the central graph.'

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from memory_contract import (
    DIRECTORY_TAG,
    DOCUMENT_TAG,
    FILE_PLACEHOLDER_TAG,
    PART_OF_LINK,
    PLACEHOLDER_TAG,
    PROJECT_TAG,
)

from memory_ingest.project import FileEntry, Manifest, build_index
from memory_ingest.state import StateDB
from memory_ingest.writer import HarnessWriter

log = logging.getLogger("memory_ingest.projectwriter")


@dataclass
class ProjectProgress:
    project: str
    total_files: int = 0
    done_files: int = 0
    skipped: int = 0
    placeholders: int = 0
    documents: int = 0
    dirs: int = 0
    current: str = ""
    finished: bool = False
    error: str | None = None
    root_lineage: str = ""
    chunks: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "project": self.project, "total_files": self.total_files,
            "done_files": self.done_files, "skipped_unchanged": self.skipped,
            "documents": self.documents, "placeholders": self.placeholders,
            "directories": self.dirs, "chunks": self.chunks,
            "current": self.current, "finished": self.finished,
            "error": self.error, "root_lineage": self.root_lineage,
        }


class ProjectLedger:
    '`project · relpath` → the node we minted for it, plus the content sha.'

    def __init__(self, path: str) -> None:
        self._db = StateDB(path)
        self._db._con.execute(
            "CREATE TABLE IF NOT EXISTS project_nodes ("
            " project TEXT NOT NULL, relpath TEXT NOT NULL, kind TEXT NOT NULL,"
            " lineage_id TEXT NOT NULL, sha TEXT NOT NULL DEFAULT '',"
            " chunk_lineages TEXT NOT NULL DEFAULT '[]',"
            " PRIMARY KEY (project, relpath))"
        )
        self._db._con.commit()

    def close(self) -> None:
        self._db.close()

    def get(self, project: str, relpath: str) -> dict | None:
        row = self._db._con.execute(
            "SELECT kind, lineage_id, sha, chunk_lineages FROM project_nodes"
            " WHERE project=? AND relpath=?",
            (project, relpath),
        ).fetchone()
        if row is None:
            return None
        return {"kind": row[0], "lineage_id": row[1], "sha": row[2],
                "chunk_lineages": json.loads(row[3])}

    def put(self, project: str, relpath: str, *, kind: str, lineage_id: str,
            sha: str = "", chunk_lineages: list[str] | None = None) -> None:
        self._db._con.execute(
            "INSERT INTO project_nodes(project, relpath, kind, lineage_id, sha,"
            " chunk_lineages) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(project, relpath) DO UPDATE SET kind=excluded.kind,"
            " lineage_id=excluded.lineage_id, sha=excluded.sha,"
            " chunk_lineages=excluded.chunk_lineages",
            (project, relpath, kind, lineage_id, sha,
             json.dumps(chunk_lineages or [])),
        )
        self._db._con.commit()

    def content_lineages(self, project: str) -> list[str]:
        import json as _json

        out: set[str] = set()
        for kind, lineage, chunks in self._db._con.execute(
            "SELECT kind, lineage_id, chunk_lineages FROM project_nodes"
            " WHERE project=?", (project,)
        ):
            if str(kind) not in ("document", "file"):
                continue
            if lineage:
                out.add(str(lineage))
            for ch in _json.loads(str(chunks or "[]")):
                out.add(str(ch))
        return sorted(out)

    def paths(self, project: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for rel, kind, lineage, sha in self._db._con.execute(
            "SELECT relpath, kind, lineage_id, sha FROM project_nodes WHERE project=?",
            (project,),
        ):
            out[str(rel)] = {"kind": kind, "lineage_id": lineage, "sha": sha}
        return out

    def projects(self) -> list[str]:
        return [str(r[0]) for r in self._db._con.execute(
            "SELECT DISTINCT project FROM project_nodes ORDER BY project")]

    def project_maps(self) -> list[tuple[str, str]]:
        return [(str(p), str(lin)) for p, lin in self._db._con.execute(
            "SELECT project, lineage_id FROM project_nodes WHERE kind='project'"
            " ORDER BY project")]

    def forget(self, project: str) -> int:
        'Drop a project from the ledger. Returns rows removed.'
        cur = self._db._con.execute(
            "DELETE FROM project_nodes WHERE project=?", (project,))
        self._db._con.commit()
        return int(cur.rowcount)


def _slug(name: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"


class ProjectWriter:

    def __init__(self, writer: HarnessWriter, ledger: ProjectLedger, scope: str) -> None:
        self.writer = writer
        self.ledger = ledger
        self.scope = scope

    def _entity(self, project: str) -> str:
        slug = _slug(project)
        return self.writer.register_entity(
            "collection", project, slug, f"project: {project}", aliases=[slug]
        )

    def write_root(self, manifest: Manifest, progress: ProjectProgress) -> str:
        entity = self._entity(manifest.project)
        slug = _slug(manifest.project)
        prior = self.ledger.get(manifest.project, "")
        lineage = self.writer.write_parent(
            body=build_index(manifest),
            entity_id=entity,
            scope=self.scope,
            tags=[PROJECT_TAG, f"project:{slug}", f"root:{manifest.root}"],
            memory_id=prior["lineage_id"] if prior else None,
        )
        self.ledger.put(manifest.project, "", kind=PROJECT_TAG,
                        lineage_id=lineage, sha=manifest.sha)
        progress.root_lineage = lineage
        return lineage

    def write_dirs(self, manifest: Manifest, progress: ProjectProgress) -> dict[str, str]:
        entity = self._entity(manifest.project)
        slug = _slug(manifest.project)
        lineages: dict[str, str] = {"": progress.root_lineage}
        for d in sorted(manifest.dirs, key=lambda x: x.relpath):
            if d.relpath == "":
                continue
            prior = self.ledger.get(manifest.project, d.relpath)
            lineage = self.writer.write_parent(
                body=f"[directory] {d.relpath}\nproject: {manifest.project}",
                entity_id=entity,
                scope=self.scope,
                tags=[DIRECTORY_TAG, f"project:{slug}", f"path:{d.relpath}"],
                memory_id=prior["lineage_id"] if prior else None,
            )
            lineages[d.relpath] = lineage
            if prior is None:
                parent = lineages.get(d.parent or "", progress.root_lineage)
                self.writer.link(lineage, parent, PART_OF_LINK)
            self.ledger.put(manifest.project, d.relpath, kind=DIRECTORY_TAG,
                            lineage_id=lineage)
            progress.dirs += 1
        return lineages

    def write_placeholder(
        self, manifest: Manifest, f: FileEntry, dir_lineage: str,
        progress: ProjectProgress,
    ) -> str:
        entity = self._entity(manifest.project)
        slug = _slug(manifest.project)
        prior = self.ledger.get(manifest.project, f.relpath)
        if prior and prior["sha"] == f.sha:
            progress.skipped += 1
            return str(prior["lineage_id"])
        lineage = self.writer.write_parent(
            body=(f"[file] {f.relpath}\nproject: {manifest.project}\n"
                  f"type: {f.mime} · {f.bytes} bytes\n"
                  f"not embedded — this file type is recorded, not read"),
            entity_id=entity,
            scope=self.scope,
            tags=[FILE_PLACEHOLDER_TAG, PLACEHOLDER_TAG, f"project:{slug}",
                  f"path:{f.relpath}", f"mime:{f.mime}", f"bytes:{f.bytes}"],
            memory_id=prior["lineage_id"] if prior else None,
        )
        if prior is None:
            self.writer.link(lineage, dir_lineage, PART_OF_LINK)
        self.ledger.put(manifest.project, f.relpath, kind=PLACEHOLDER_TAG,
                        lineage_id=lineage, sha=f.sha)
        progress.placeholders += 1
        return lineage
