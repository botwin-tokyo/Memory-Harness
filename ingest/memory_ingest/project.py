'Scanning a project folder into a manifest + an INDEX map. No writes, no network.'

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

from memory_contract import FileKind, classify_file

DEFAULT_IGNORE: tuple[str, ...] = (
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".next",
    ".cache", ".DS_Store", ".idea", ".vscode", "target", ".tox",
)

_HASH_CHUNK = 1 << 20


@dataclass
class FileEntry:
    relpath: str
    kind: FileKind
    mime: str
    bytes: int
    sha: str

    @property
    def name(self) -> str:
        return self.relpath.rsplit("/", 1)[-1]


@dataclass
class DirEntry:
    relpath: str
    parent: str | None


@dataclass
class Manifest:
    project: str
    root: str
    dirs: list[DirEntry] = field(default_factory=list)
    files: list[FileEntry] = field(default_factory=list)
    generated_at: str = ""

    @property
    def sha(self) -> str:
        h = hashlib.sha256()
        for d in self.dirs:
            h.update(f"D:{d.relpath}\n".encode())
        for f in self.files:
            h.update(f"F:{f.relpath}:{f.kind}:{f.bytes}:{f.sha}\n".encode())
        return h.hexdigest()

    def by_kind(self, kind: FileKind) -> list[FileEntry]:
        return [f for f in self.files if f.kind == kind]


def _mime_for(path: str) -> str:
    import mimetypes

    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def _sha_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def scan_project(
    root: str | Path,
    project: str | None = None,
    ignore: tuple[str, ...] | list[str] = DEFAULT_IGNORE,
) -> Manifest:
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise NotADirectoryError(f"not a directory: {base}")
    skip = set(ignore)
    name = project or base.name

    manifest = Manifest(
        project=name,
        root=str(base),
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    )
    manifest.dirs.append(DirEntry(relpath="", parent=None))

    def walk(current: Path, rel: str) -> None:
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except PermissionError:
            return
        for entry in entries:
            if entry.name in skip or entry.name.startswith("."):
                continue
            child_rel = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir() and not entry.is_symlink():
                manifest.dirs.append(DirEntry(relpath=child_rel, parent=rel))
                walk(entry, child_rel)
            elif entry.is_file() and not entry.is_symlink():
                manifest.files.append(
                    FileEntry(
                        relpath=child_rel,
                        kind=classify_file(child_rel),
                        mime=_mime_for(child_rel),
                        bytes=entry.stat().st_size,
                        sha=_sha_of(entry),
                    )
                )

    walk(base, "")
    return manifest


def build_index(manifest: Manifest) -> str:
    counts: dict[str, int] = {}
    for f in manifest.files:
        counts[f.kind] = counts.get(f.kind, 0) + 1
    summary = " · ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "empty"

    lines = [
        f"# PROJECT — {manifest.project}",
        "",
        f"- root: {manifest.root}",
        f"- generated_at: {manifest.generated_at}",
        f"- directories: {len(manifest.dirs)} · files: {len(manifest.files)}",
        f"- content: {summary}",
        f"- manifest-sha: {manifest.sha}",
        "",
        "## Tree",
        "```",
    ]

    children: dict[str, list[str]] = {}
    for d in manifest.dirs:
        if d.parent is not None:
            children.setdefault(d.parent, []).append(d.relpath)
    files_by_dir: dict[str, list[FileEntry]] = {}
    for f in manifest.files:
        parent = f.relpath.rsplit("/", 1)[0] if "/" in f.relpath else ""
        files_by_dir.setdefault(parent, []).append(f)

    def render(rel: str, depth: int) -> None:
        pad = "    " * depth
        for child in sorted(children.get(rel, [])):
            lines.append(f"{pad}{child.rsplit('/', 1)[-1]}/")
            render(child, depth + 1)
        for f in sorted(files_by_dir.get(rel, []), key=lambda x: x.relpath):
            mark = "" if f.kind != "placeholder" else "  (not read — placeholder)"
            lines.append(f"{pad}{f.name}  [{f.kind}, {f.bytes}B]{mark}")

    render("", 0)
    lines += ["```", ""]
    return "\n".join(lines)
