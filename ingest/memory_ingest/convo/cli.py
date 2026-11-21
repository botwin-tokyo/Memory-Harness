from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from memory_contract import CONVERSATION_SOURCES

from memory_ingest.config import Settings, get_settings
from memory_ingest.convo.sync import build_adapter, sync_source
from memory_ingest.convo.writer import ConversationLedger
from memory_ingest.writer import HarnessWriter

log = logging.getLogger("memory_convo_sync")

DEBOUNCE_SECONDS = 20.0
POLL_SECONDS = 5.0


def _settings() -> Settings:
    return get_settings()


def _sources(requested: str | None) -> list[str]:
    if requested and requested != "all":
        return [requested]
    return list(CONVERSATION_SOURCES)


def _exists(source: str, path: str | None) -> bool:
    'Is this source actually present on this node? Absent is normal.'
    try:
        adapter = build_adapter(source, path=path)
    except ValueError:
        return False
    if source == "hermes":
        return Path(getattr(adapter, "db_path")).is_file()
    return bool(getattr(adapter, "session_files")())


def run_once(settings: Settings, sources: list[str], *,
             path: str | None = None, agent: str | None = None,
             full: bool = False) -> dict:
    'One sweep across every source that exists here.'
    ledger = ConversationLedger(settings.state_db)
    writer = HarnessWriter(settings.harness_base, settings.harness_key)
    out: dict[str, dict] = {}
    try:
        for source in sources:
            if not _exists(source, path):
                log.info("%s: not present on this node — skipping", source)
                out[source] = {"source": source, "present": False}
                continue
            out[source] = sync_source(
                source, ledger=ledger, writer=writer,
                scope=settings.artifact_scope, path=path, agent=agent, full=full,
            )
    finally:
        writer.close()
        ledger.close()
    return out


def _fingerprint(sources: list[str], path: str | None) -> tuple:
    'Cheap "has anything changed" signal: sizes and mtimes of the stores.'
    marks: list[tuple] = []
    for source in sources:
        try:
            adapter = build_adapter(source, path=path)
        except ValueError:
            continue
        files: list[Path] = []
        if source == "hermes":
            db = Path(getattr(adapter, "db_path"))
            files = [db, Path(str(db) + "-wal")]
        else:
            files = list(getattr(adapter, "session_files")())
        for f in files:
            try:
                st = f.stat()
                marks.append((str(f), st.st_size, st.st_mtime_ns))
            except OSError:
                continue
    return tuple(sorted(marks))


def watch(settings: Settings, sources: list[str], *, path: str | None = None,
          agent: str | None = None, poll: float = POLL_SECONDS,
          debounce: float = DEBOUNCE_SECONDS, iterations: int | None = None) -> None:
    'Near-live: sweep shortly after the stores stop changing.'
    last = _fingerprint(sources, path)
    pending_since: float | None = None
    n = 0
    log.info("watching %s (poll %.0fs, debounce %.0fs)", ", ".join(sources),
             poll, debounce)
    while iterations is None or n < iterations:
        n += 1
        time.sleep(poll)
        now = _fingerprint(sources, path)
        if now != last:
            last = now
            pending_since = time.monotonic()
            continue
        if pending_since is not None and time.monotonic() - pending_since >= debounce:
            pending_since = None
            try:
                result = run_once(settings, sources, path=path, agent=agent)
                log.info("swept: %s", json.dumps(
                    {k: v.get("turns", 0) for k, v in result.items()}))
            except Exception:  # noqa: BLE001 - a watcher must outlive one failure
                log.exception("sweep failed — will try again on the next change")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="memory-convo-sync",
        description="Mirror this node's agent conversations into the central graph.")
    ap.add_argument("--source", choices=CONVERSATION_SOURCES, required=True)
    ap.add_argument("--path", required=True,
                    help="override the source's location (a state.db or a sessions dir)")
    ap.add_argument("--agent", default=None, help="label these conversations")
    ap.add_argument("--full", action="store_true",
                    help="ignore stored cursors and re-read every session")
    ap.add_argument("--watch", action="store_true",
                    help="stay running and sweep shortly after each change")
    ap.add_argument("--poll", type=float, default=POLL_SECONDS)
    ap.add_argument("--debounce", type=float, default=DEBOUNCE_SECONDS)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = _settings()
    sources = _sources(args.source)

    if args.watch:
        watch(settings, sources, path=args.path, agent=args.agent,
              poll=args.poll, debounce=args.debounce)
        return 0

    result = run_once(settings, sources, path=args.path, agent=args.agent,
                      full=args.full)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for source, r in result.items():
            if not r.get("present", True):
                print(f"{source}: not present on this node")
                continue
            print(f"{source}: {r['sessions_seen']} session(s), {r['turns']} new turn(s), "
                  f"{r['chunks']} chunk(s), {r['redacted_chunks']} redacted"
                  + (f", {len(r['failed'])} FAILED" if r.get("failed") else ""))
    failed = any(r.get("failed") for r in result.values())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
