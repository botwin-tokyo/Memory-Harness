from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import logging
import uvicorn
from fastapi import FastAPI

from memory_harness.clients.embeddings import EmbeddingsClient
from memory_harness.clients.llm import LlmClient
from memory_harness.config import Settings, get_settings

CURATOR_PORT = 8090
CURATE_INTERVAL_S = 60.0

CURSOR_FILE = Path(".state/curator-state.json")

log = logging.getLogger("memory_harness.curator")


def _load_state() -> dict:
    'The rotation cursors, across restarts.'
    try:
        return json.loads(CURSOR_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(extra: dict) -> None:
    try:
        CURSOR_FILE.write_text(json.dumps(
            {k: v for k, v in extra.items() if isinstance(v, (int, float, str))}))
    except OSError as exc:
        log.warning("could not persist curator cursors: %s", exc)


@dataclass
class Curator:
    "The curator's clients + curation cursor."

    harness: httpx.Client
    key: str
    embeddings: EmbeddingsClient
    llm: LlmClient
    cursor: int = 0
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}"}


def _book_pass(curator: "Curator") -> None:
    'One book-linking pass against a read-only connection.'
    from memory_harness.curator import booklinks
    from memory_harness.db.connection import open_reader

    con = open_reader(get_settings().db.path)
    try:
        booklinks.propose(curator, con)
    finally:
        con.close()


def _journal_fact_pass(curator: "Curator") -> None:
    'One nightly journal → proposed facts pass. Bounded; never mints.'
    from memory_harness.curator import journalfacts

    journalfacts.propose_from_journals(curator)


def _people_pass(curator: "Curator") -> None:
    'One nightly person pass. Proposes names and merges; creates nobody.'
    from memory_harness.curator import peoplepass

    peoplepass.propose_people(curator)


def _todo_link_pass(curator: "Curator") -> None:
    from memory_harness.curator import todolinks

    todolinks.pair_todo_days(curator)


def _blog_link_pass(curator: "Curator") -> None:
    from memory_harness.curator import bloglinks

    bloglinks.pair_blog_posts(curator)


def create_curator_app(curator: Curator, *, interval_s: float = CURATE_INTERVAL_S) -> FastAPI:
    'The local ops app: skills routes + the curation loop in lifespan.'

    from memory_harness.curator.skills import router as skills_router

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        from memory_harness.curator.links import curate_links
        from memory_harness.curator import booklinks

        async def loop() -> None:
            last_booklinks: float = 0.0
            while True:
                try:
                    await asyncio.to_thread(curate_links, curator)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass

                now = asyncio.get_running_loop().time()
                if booklinks.is_off_hours() and now - last_booklinks > 6 * 3600:
                    last_booklinks = now
                    log.info("nightly window: starting")
                    for name, fn in (("booklinks", _book_pass),
                                     ("journalfacts", _journal_fact_pass),
                                     ("people", _people_pass),
                                     ("todolinks", _todo_link_pass),
                                     ("bloglinks", _blog_link_pass)):
                        try:
                            await asyncio.to_thread(fn, curator)
                            log.info("nightly window: %s done", name)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:  # noqa: BLE001
                            log.warning("nightly window: %s FAILED: %s: %s",
                                        name, type(exc).__name__, exc)
                    curator.extra["last_nightly_run"] = time.time()
                    _save_state(curator.extra)
                    log.info("nightly window: finished")

                await asyncio.sleep(interval_s)

        task = asyncio.create_task(loop())
        app.state.curator = curator
        yield
        task.cancel()

    app = FastAPI(docs_url=None, redoc_url=None, title="memory-curator", lifespan=lifespan)
    app.include_router(skills_router)
    return app


def build_curator(settings: Settings, key: str, harness_base_url: str) -> Curator:
    return Curator(
        harness=httpx.Client(base_url=harness_base_url, timeout=30.0),
        key=key,
        embeddings=EmbeddingsClient(
            settings.embeddings.base_url,
            settings.embeddings.api_key,
            timeout_s=settings.embeddings.timeout_s,
            dim=settings.embeddings.dim,
            max_batch=settings.embeddings.max_batch,
        ),
        llm=LlmClient(
            settings.llm.base_url,
            settings.llm.model,
            settings.llm.api_key,
            max_tokens=settings.llm.max_tokens,
            timeout_s=settings.llm.timeout_s,
        ),
    )


def run() -> None:
    'Entry point: `python -m memory_harness.curator.main` (systemd unit).'
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    key = os.environ.get("CURATOR_API_KEY", "")
    if not key:
        raise SystemExit("CURATOR_API_KEY is not set (mint a trusted_tool key for the curator)")
    curator = build_curator(settings, key, f"http://127.0.0.1:{settings.server.port}")
    curator.extra.update(_load_state())
    log.info("curator started; cursors restored: %s", dict(curator.extra))
    app = create_curator_app(curator)
    uvicorn.run(app, host="127.0.0.1", port=CURATOR_PORT, workers=1)


if __name__ == "__main__":
    run()
