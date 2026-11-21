'Headless-Chromium print-to-PDF (Option C) for JS-heavy/weird pages.'

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from memory_ingest.config import Settings

log = logging.getLogger("memory_ingest.render")


class RenderError(RuntimeError):
    pass


def _scratch_root() -> Path:
    snap_common = Path.home() / "snap" / "chromium" / "common"
    if snap_common.is_dir():
        return snap_common / "memory-ingest"
    return Path.home() / ".cache" / "memory-ingest"


def render_url_to_pdf(url: str, settings: Settings) -> bytes:
    scratch_root = _scratch_root()
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch_root) as tmp:
        out = Path(tmp) / "page.pdf"
        cmd = [
            settings.chromium_bin,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--disable-extensions",
            "--disable-sync",
            f"--user-data-dir={tmp}/profile",
            "--virtual-time-budget=10000",
            f"--print-to-pdf={out}",
            "--no-pdf-header-footer",
            url,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=settings.render_timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise RenderError(f"render timed out after {settings.render_timeout_s}s") from exc
        except FileNotFoundError as exc:
            raise RenderError(f"chromium not found: {settings.chromium_bin}") from exc
        if not out.is_file() or out.stat().st_size == 0:
            tail = (proc.stderr or b"")[-400:].decode(errors="replace")
            raise RenderError(f"chromium produced no PDF (rc={proc.returncode}): {tail}")
        return out.read_bytes()
