'The three git tiers, orchestrated over the existing pipeline pieces.'

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import memory_contract as contract

from memory_ingest import gitrepo
from memory_ingest.chunker import chunk_text
from memory_ingest.config import Settings
from memory_ingest.gitindex import build_index
from memory_ingest.llm import Summarizer
from memory_ingest.pipeline import Ingestor
from memory_ingest.state import GitState

log = logging.getLogger("memory_ingest.gitservice")


@dataclass
class GitIndexResult:
    repo: str
    sha: str
    revision: int
    files: int
    summarized: int
    index_parent: str
    repo_lineage: str
    deduped: bool
    grounded_to: str | None = None


@dataclass
class RepoIngestResult:
    repo: str
    sha: str
    files_written: int
    files_revised: int
    files_forgotten: int
    chunks: int
    repo_lineage: str
    grounded_to: str | None = None
    skipped_unchanged: int = 0
    file_parents: list[str] = field(default_factory=list)


@dataclass
class GitActivityResult:
    repo: str
    commits_written: int
    commits_existing: int
    chained: int
    modifies_links: int
    repo_lineage: str
    window: int
    newest_sha: str | None = None
    commit_lineages: list[str] = field(default_factory=list)


class GitService:
    def __init__(
        self,
        settings: Settings,
        ingestor: Ingestor,
        state: GitState,
        summarizer: Summarizer,
    ) -> None:
        self.settings = settings
        self.ingestor = ingestor
        self.writer = ingestor.writer
        self.state = state
        self.summarizer = summarizer


    def _ensure_repo_node(self, full: str, slug: str) -> str:
        entity_id = self.writer.register_entity(
            "repo", full, slug, f"source: https://github.com/{full}",
            aliases=[slug],
        )
        prior = self.state.repo_get(full)
        if prior and prior.get("repo_lineage"):
            return str(prior["repo_lineage"])
        lineage = self.writer.write_parent(
            body=f"[repo] {full}\nsource: https://github.com/{full}",
            entity_id=entity_id,
            scope=self.settings.artifact_scope,
            tags=[contract.REPO_TAG],
        )
        self.state.repo_put(
            full, head_sha=prior["head_sha"] if prior else "",
            index_revision=prior["index_revision"] if prior else 0,
            repo_lineage=lineage,
        )
        return lineage


    def index_repo(self, repo: str, link_to: str | None = None) -> GitIndexResult:
        full, _, slug = gitrepo.normalize_repo(repo)
        gitrepo.check_allowed(full, self.settings)
        repo_lineage = self._ensure_repo_node(full, slug)

        localdir = gitrepo.clone_or_update(full, self.settings, shallow=True)
        sha = gitrepo.head_sha(localdir)
        prior = self.state.repo_get(full)
        if prior and prior["head_sha"] == sha and prior["index_revision"] > 0:
            log.info("index %s: sha unchanged (%s) — no-op", full, sha[:10])
            index_file = localdir / "INDEX.md"
            if not index_file.is_file():
                log.info("index %s: restoring INDEX.md at rev %d from cache",
                         full, prior["index_revision"])
                build_index(full, self.settings, self.state, self.summarizer,
                            revision=prior["index_revision"])
            ingest_state = self.state.get(str(localdir / "INDEX.md"))
            index_parent = ingest_state["parent_lineage"] if ingest_state else ""
            if link_to and index_parent:
                self.writer.ground(link_to, index_parent)
            return GitIndexResult(
                repo=full, sha=sha, revision=prior["index_revision"],
                files=0, summarized=0, index_parent=index_parent,
                repo_lineage=repo_lineage, deduped=True, grounded_to=link_to,
            )

        built = build_index(full, self.settings, self.state, self.summarizer)
        result = self.ingestor.ingest(
            str(built.index_path),
            kind="repo-index",
            title=f"{full} INDEX",
            scope=self.settings.artifact_scope,
            link_to=repo_lineage,
            entity_id=f"repo:{slug}",
        )
        if link_to:
            self.writer.ground(link_to, result.parent_lineage)
        self.state.repo_put(
            full, head_sha=built.sha, index_revision=built.revision,
            repo_lineage=repo_lineage,
        )
        return GitIndexResult(
            repo=full, sha=built.sha, revision=built.revision,
            files=built.files, summarized=built.summarized,
            index_parent=result.parent_lineage, repo_lineage=repo_lineage,
            deduped=False, grounded_to=link_to,
        )


    def fetch_file(self, repo: str, path: str, sha: str | None = None) -> dict[str, Any]:
        full, _, _ = gitrepo.normalize_repo(repo)
        gitrepo.check_allowed(full, self.settings)
        localdir = gitrepo.clone_or_update(full, self.settings, shallow=True)
        prior = self.state.repo_get(full)
        pinned = sha or (prior["head_sha"] if prior and prior["head_sha"] else None)
        content = gitrepo.show_file(localdir, path, pinned)
        return {
            "repo": full, "path": path,
            "sha": pinned or gitrepo.head_sha(localdir),
            "content": content,
        }


    def ingest_repo_full(self, repo: str, link_to: str | None = None) -> RepoIngestResult:
        full, _, slug = gitrepo.normalize_repo(repo)
        gitrepo.check_allowed(full, self.settings)
        repo_lineage = self._ensure_repo_node(full, slug)
        entity_id = f"repo:{slug}"
        scope = self.settings.artifact_scope

        localdir = gitrepo.clone_or_update(full, self.settings)
        sha = gitrepo.head_sha(localdir)
        files = [(p, s) for p, s in gitrepo.walk_source_files(localdir)
                 if p != "INDEX.md"]

        written = revised = skipped = chunks_total = 0
        parents: list[str] = []
        seen_paths = set()
        for path, blob_sha in sorted(files):
            seen_paths.add(path)
            prior = self.state.git_file_get(full, path)
            if prior and prior["content_sha"] == blob_sha:
                skipped += 1
                continue
            text = gitrepo.show_file(localdir, path, sha)
            chunks = chunk_text(text, self.settings.chunk_tokens,
                                self.settings.chunk_overlap_tokens)
            parent = self.writer.write_parent(
                body=(f"[file] {full}:{path}\nref={full}@{sha}:{path}\n"
                      f"chunks: {len(chunks)}"),
                entity_id=entity_id, scope=scope,
                tags=[contract.GIT_FILE_TAG, path],
                memory_id=prior["parent_lineage"] if prior else None,
            )
            old_chunks: list[str] = prior["chunk_lineages"] if prior else []
            chunk_lineages: list[str] = []
            for i, body in enumerate(chunks):
                chunk_lineages.append(self.writer.write_chunk(
                    body=body, entity_id=entity_id, scope=scope,
                    parent_lineage=parent, index=i,
                    memory_id=old_chunks[i] if i < len(old_chunks) else None,
                ))
            for stale in old_chunks[len(chunks):]:
                self.writer.forget(stale, f"re-ingest: {path} shrank")
            if prior is None:
                self.writer.link(parent, repo_lineage, "part_of")
                written += 1
            else:
                revised += 1
            self.state.git_file_put(
                full, path, content_sha=blob_sha,
                parent_lineage=parent, chunk_lineages=chunk_lineages,
            )
            parents.append(parent)
            chunks_total += len(chunks)

        forgotten = 0
        for path in self.state.git_file_paths(full):
            if path in seen_paths:
                continue
            prior = self.state.git_file_get(full, path)
            if prior:
                self.writer.forget(prior["parent_lineage"],
                                   f"file deleted upstream: {path}")
                for c in prior["chunk_lineages"]:
                    self.writer.forget(c, f"file deleted upstream: {path}")
            self.state.git_file_delete(full, path)
            forgotten += 1

        if link_to:
            self.writer.ground(link_to, repo_lineage)
        log.info("ingest_repo %s@%s: +%d ~%d -%d (=%d unchanged, %d chunks)",
                 full, sha[:10], written, revised, forgotten, skipped, chunks_total)
        return RepoIngestResult(
            repo=full, sha=sha, files_written=written, files_revised=revised,
            files_forgotten=forgotten, chunks=chunks_total,
            repo_lineage=repo_lineage, grounded_to=link_to,
            skipped_unchanged=skipped, file_parents=parents,
        )


    @staticmethod
    def _commit_body(c: gitrepo.Commit) -> str:
        files = "\n".join(f"  {f}" for f in c.files[:40])
        more = f"\n  … {len(c.files) - 40} more" if len(c.files) > 40 else ""
        return (
            f"[commit] {c.sha[:10]} · {c.date} · {c.author}\n"
            f"{c.message}\n"
            f"files ({len(c.files)}):\n{files}{more}"
        )

    def commit_activity(
        self, repo: str, window: int = 30, full: bool = False
    ) -> GitActivityResult:
        full_name, _, slug = gitrepo.normalize_repo(repo)
        gitrepo.check_allowed(full_name, self.settings)
        repo_lineage = self._ensure_repo_node(full_name, slug)
        localdir = gitrepo.clone_or_update(full_name, self.settings, shallow=not full)
        n = 10_000 if full else window
        commits = gitrepo.recent_commits(localdir, n)

        written = existing = chained = modified = 0
        lineages: list[str] = []
        fresh: set[str] = set()
        for c in commits:
            lineage = self.state.commit_get(full_name, c.sha)
            if lineage is not None:
                existing += 1
                lineages.append(lineage)
                continue
            lineage = self.writer.write_parent(
                body=self._commit_body(c),
                entity_id=f"repo:{slug}",
                scope=self.settings.artifact_scope,
                tags=[contract.COMMIT_TAG, f"sha:{c.sha[:10]}"],
            )
            self.state.commit_put(full_name, c.sha, lineage)
            self.writer.link(lineage, repo_lineage, "part_of")
            written += 1
            fresh.add(c.sha)
            lineages.append(lineage)
            for path in c.files:
                f = self.state.git_file_get(full_name, path)
                if f:
                    self.writer.link(lineage, f["parent_lineage"], "modifies")
                    modified += 1

        for child_c, parent_c in zip(commits, commits[1:]):
            if child_c.sha not in fresh and parent_c.sha not in fresh:
                continue
            child = self.state.commit_get(full_name, child_c.sha)
            parent = self.state.commit_get(full_name, parent_c.sha)
            if child and parent:
                self.writer.link(child, parent, "follows")
                chained += 1

        log.info("activity %s: +%d commits (=%d known), %d chained, %d modifies",
                 full_name, written, existing, chained, modified)
        return GitActivityResult(
            repo=full_name, commits_written=written, commits_existing=existing,
            chained=chained, modifies_links=modified, repo_lineage=repo_lineage,
            window=n, newest_sha=commits[0].sha if commits else None,
            commit_lineages=lineages,
        )
