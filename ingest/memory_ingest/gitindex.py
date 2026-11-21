from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from memory_ingest import gitrepo
from memory_ingest.config import Settings
from memory_ingest.llm import Summarizer
from memory_ingest.state import GitState

log = logging.getLogger("memory_ingest.gitindex")


@dataclass
class BuiltIndex:
    repo: str
    slug: str
    localdir: Path
    index_path: Path
    sha: str
    revision: int
    files: int
    summarized: int


def _tree_lines(paths: list[str]) -> list[str]:
    'A compact dir tree: directories once, files indented under them.'
    lines: list[str] = []
    seen_dirs: set[str] = set()
    for path in sorted(paths):
        parts = Path(path).parts
        for depth in range(len(parts) - 1):
            d = "/".join(parts[: depth + 1])
            if d not in seen_dirs:
                seen_dirs.add(d)
                lines.append("    " * depth + f"{parts[depth]}/")
        lines.append("    " * (len(parts) - 1) + parts[-1])
    return lines


def build_index(
    repo: str,
    settings: Settings,
    state: GitState,
    summarizer: Summarizer,
    revision: int | None = None,
) -> BuiltIndex:
    full, _, slug = gitrepo.normalize_repo(repo)
    localdir = gitrepo.clone_or_update(full, settings, shallow=True)
    sha = gitrepo.head_sha(localdir)
    files = gitrepo.walk_source_files(localdir)
    files = [(p, s) for p, s in files if p != "INDEX.md"]

    if revision is None:
        prior = state.repo_get(full)
        revision = (prior["index_revision"] + 1) if prior else 1

    summarized = 0
    file_lines: list[str] = []
    for path, blob_sha in sorted(files):
        summary = state.summary_get(full, path, blob_sha)
        if summary is None:
            text = gitrepo.show_file(localdir, path, sha)
            summary = summarizer.summarize(path, text)
            state.summary_put(full, path, blob_sha, summary)
            summarized += 1
        updated = gitrepo.file_last_commit(localdir, path)
        file_lines.append(
            f"- `{path}` · {updated} · {summary} · ref={full}@{sha}:{path}"
        )
    log.info("index %s: %d files, %d freshly summarized", full, len(files), summarized)

    commits = gitrepo.recent_commits(localdir, 15)
    commit_lines = [
        f"- `{c.sha[:10]}` {c.date} {c.author}: {c.message}"
        + (f" ({', '.join(c.files[:6])}{', …' if len(c.files) > 6 else ''})"
           if c.files else "")
        for c in commits
    ]

    generated = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    index_md = "\n".join([
        f"# INDEX — {full}",
        "",
        f"- harness index revision: {revision}",
        f"- generated_at: {generated}",
        f"- sha: {sha}",
        f"- files: {len(files)}",
        "",
        "## Tree",
        "```",
        *_tree_lines([p for p, _ in files]),
        "```",
        "",
        "## Files",
        *file_lines,
        "",
        "## Recent commits",
        *commit_lines,
        "",
    ])

    index_path = localdir / "INDEX.md"
    index_path.write_text(index_md, encoding="utf-8")
    if settings.git_push_index:
        subprocess.run(["git", "add", "--", "INDEX.md"], cwd=localdir, check=True)
        subprocess.run(["git", "commit", "-m", f"Update index revision {revision}"],
                       cwd=localdir, check=True)
        subprocess.run(["git", "push"], cwd=localdir, check=True)

    return BuiltIndex(
        repo=full, slug=slug, localdir=localdir, index_path=index_path,
        sha=sha, revision=revision, files=len(files), summarized=summarized,
    )
