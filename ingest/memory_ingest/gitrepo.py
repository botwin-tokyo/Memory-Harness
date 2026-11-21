from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from memory_ingest.config import Settings

DENY_DIRS = {
    "node_modules", "dist", "build", ".venv", "venv", "target", "__pycache__",
    ".git", ".next", ".cache", "vendor", "coverage", ".pytest_cache",
    ".mypy_cache", "site-packages",
}
DENY_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb",
    "poetry.lock", "uv.lock", "Cargo.lock", "composer.lock", "Gemfile.lock",
}
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff", ".svgz",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".jar", ".war",
    ".so", ".dylib", ".dll", ".exe", ".bin", ".dat", ".db", ".sqlite",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".wav", ".ogg",
    ".mov", ".avi", ".webm", ".pyc", ".class", ".o", ".a", ".wasm", ".onnx",
    ".safetensors", ".pt", ".pth", ".npy", ".parquet",
}


class GitRefused(ValueError):
    'Access refused: off-allowlist, unreachable, or outside the repo.'


@dataclass
class Commit:
    sha: str
    date: str
    author: str
    message: str
    files: list[str]


def _run(args: list[str], cwd: str | Path | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=600,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home()),
             "GIT_TERMINAL_PROMPT": "0"},
    )
    if proc.returncode != 0:
        raise GitRefused(f"git {args[0]} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def normalize_repo(repo: str) -> tuple[str, str, str]:
    "'owner/name' | github URL → (owner/name, clone_url, slug)."
    r = repo.strip().rstrip("/")
    if r.startswith(("http://", "https://")):
        parts = urlparse(r).path.strip("/").removesuffix(".git").split("/")
        if len(parts) < 2:
            raise GitRefused(f"cannot parse repo from url: {repo}")
        full = "/".join(parts[:2])
    elif r.count("/") == 1:
        full = r.removesuffix(".git")
    else:
        raise GitRefused(f"repo must be owner/name or a github url: {repo}")
    slug = full.lower().replace("/", "-")
    return full, f"https://github.com/{full}.git", slug


def check_allowed(full: str, settings: Settings) -> None:
    allow = [a.strip().lower() for a in settings.git_allowlist.split(",") if a.strip()]
    if not allow:
        raise GitRefused("Configure GIT_ALLOWLIST before importing repositories")
    owner = full.split("/")[0].lower()
    if owner in allow or full.lower() in allow:
        return
    raise GitRefused(f"repo {full} is not on GIT_ALLOWLIST")


def clone_dir(settings: Settings, slug: str) -> Path:
    return Path(settings.state_db).parent / "repos" / slug


def clone_or_update(repo: str, settings: Settings, *, shallow: bool = False) -> Path:
    full, url, slug = normalize_repo(repo)
    check_allowed(full, settings)
    dest = clone_dir(settings, slug)
    if (dest / ".git").is_dir():
        _run(["fetch", "--prune", "origin"], cwd=dest)
        head = _run(["symbolic-ref", "refs/remotes/origin/HEAD", "--short"], cwd=dest).strip()
        _run(["reset", "--hard", head], cwd=dest)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone", "--quiet"]
    if shallow:
        args += ["--depth", "50"]
    _run([*args, url, str(dest)])
    return dest


def head_sha(localdir: Path) -> str:
    return _run(["rev-parse", "HEAD"], cwd=localdir).strip()


def file_last_commit(localdir: Path, path: str) -> str:
    "ISO date of the last commit touching the path ('' if unknown)."
    return _run(["log", "-1", "--format=%cs", "--", path], cwd=localdir).strip()


def recent_commits(localdir: Path, n: int = 15) -> list[Commit]:
    raw = _run(
        ["log", f"-{n}", "--name-only", "--format=%x1e%H%x1f%cs%x1f%an%x1f%s"],
        cwd=localdir,
    )
    commits: list[Commit] = []
    for block in raw.split("\x1e"):
        block = block.strip()
        if not block:
            continue
        head, _, files = block.partition("\n")
        sha, date, author, message = head.split("\x1f")
        commits.append(Commit(
            sha=sha, date=date, author=author, message=message,
            files=[f for f in files.splitlines() if f.strip()],
        ))
    return commits


def _is_binary(localdir: Path, path: str) -> bool:
    if Path(path).suffix.lower() in BINARY_SUFFIXES:
        return True
    try:
        head = (localdir / path).open("rb").read(4096)
    except OSError:
        return True
    return b"\x00" in head


def walk_source_files(localdir: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in _run(["ls-files", "-s"], cwd=localdir).splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) < 3 or parts[0] == "160000":
            continue
        sha = parts[1]
        p = Path(path)
        if any(part in DENY_DIRS for part in p.parts):
            continue
        if p.name in DENY_FILES:
            continue
        if _is_binary(localdir, path):
            continue
        out.append((path, sha))
    return out


def show_file(localdir: Path, path: str, sha: str | None = None) -> str:
    if path.startswith("/") or ".." in Path(path).parts:
        raise GitRefused(f"path escapes the repo: {path}")
    ref = sha or "HEAD"
    return _run(["show", f"{ref}:{path}"], cwd=localdir)
