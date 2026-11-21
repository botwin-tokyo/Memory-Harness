'The local ingestion service: POST /ingest on 127.0.0.1:8100.'

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import secrets
import shutil
import tempfile

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

import dataclasses

from memory_contract import CONVERSATION_SOURCES
from memory_fleet import from_env as fleet_from_env, start as fleet_start

from memory_ingest.config import get_settings
from memory_ingest.convo.sync import sync_source
from memory_ingest.convo.writer import ConversationLedger
from memory_ingest.library.extract import slugify_title
from memory_ingest.library.service import (
    BookRefused,
    ingest_book,
    library_dir,
    store_upload,
)
from memory_ingest.blog.feed import FeedRefused, missing_from, parse_feed
from memory_ingest.blog.ledger import BlogLedger
from memory_ingest.blog.model import BlogRefused, parse_blog
from memory_ingest.blog.writer import BlogWriter
from memory_ingest.journal.model import JournalRefused, parse as parse_journal
from memory_ingest.journal.writer import JournalLedger, JournalWriter
from memory_ingest.todo.model import TodoRefused, parse_todo_day
from memory_ingest.todo.writer import TodoLedger, TodoWriter
from memory_ingest.library.writer import BookLedger, BookProgress
from memory_ingest.seed.hub import HubWriter
from memory_ingest.seed.service import NotApproved, ingest_records
from memory_ingest.seed.writer import MemoryWriter, SeedLedger
from memory_ingest.fetcher import FetchRefused, fetch_url
from memory_ingest.parse import parse_html
from memory_ingest.gitrepo import GitRefused
from memory_ingest.gitservice import GitService
from memory_ingest.llm import LlmUnavailable, Summarizer
from memory_ingest.ocr import OcrClient, OcrError
from memory_ingest.pdfpages import rasterize_page
from memory_ingest.pipeline import Ingestor
from memory_ingest.render import RenderError
from memory_ingest.project import scan_project
from memory_ingest.projectingest import ingest_project
from memory_ingest.projectwriter import ProjectLedger, ProjectProgress
from memory_ingest.state import GitState, StateDB
from memory_ingest.writer import HarnessWriter, HarnessWriteError

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("memory_ingest")

@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    'Fleet telemetry, and nothing else.'
    _app.state.fleet = None
    _app.state.fleet_task = None
    cfg = fleet_from_env("ingest")
    if cfg is not None:
        from memory_ingest.fleet_collector import build_collector

        _app.state.fleet, _app.state.fleet_task = fleet_start(cfg, build_collector())
        log.info("fleet publisher started: %s/%s", cfg.node_id, cfg.source_id)
    yield
    if _app.state.fleet is not None:
        await _app.state.fleet.stop("ingest shutdown")
        await asyncio.sleep(0.2)
        _app.state.fleet_task.cancel()


app = FastAPI(docs_url=None, redoc_url=None, title="memory-ingest", lifespan=_lifespan)
settings = get_settings()

_one_at_a_time = asyncio.Semaphore(1)
_queue_depth = 0


def build_ingestor() -> Ingestor:
    return Ingestor(
        settings,
        HarnessWriter(settings.harness_base, settings.harness_key),
        OcrClient(settings.ocr_base, settings.ocr_key),
        StateDB(settings.state_db),
    )


def build_git_service() -> GitService:
    ingestor = Ingestor(
        settings,
        HarnessWriter(settings.harness_base, settings.harness_key),
        OcrClient(settings.ocr_base, settings.ocr_key),
        GitState(settings.state_db),
    )
    return GitService(settings, ingestor, ingestor.state, Summarizer(settings))  # type: ignore[arg-type]


def _close_git(svc: GitService) -> None:
    svc.writer.close()
    svc.ingestor.ocr.close()
    svc.state.close()
    svc.summarizer.close()


class IngestIn(BaseModel):
    source: str
    kind: str = "document"
    title: str | None = None
    about: str | None = None
    scope: str | None = None
    link_to: str | None = None
    force_render: bool = False


class IngestOut(BaseModel):
    entity_id: str
    parent_lineage: str
    chunks: int
    chunk_lineages: list[str]
    route: str
    deduped: bool
    grounded_to: str | None


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "memory-ingest",
        "busy": _one_at_a_time.locked(),
        "queued": max(_queue_depth - 1, 0),
    }


@app.post("/ingest", response_model=IngestOut)
async def ingest(body: IngestIn) -> IngestOut:
    global _queue_depth
    _queue_depth += 1
    if _one_at_a_time.locked():
        log.info("ingest queued (%d ahead): %s", _queue_depth - 1, body.source)
    try:
        async with _one_at_a_time:
            return await asyncio.to_thread(_run_pipeline, body)
    finally:
        _queue_depth -= 1


def _run_pipeline(body: IngestIn) -> IngestOut:
    ingestor = build_ingestor()
    try:
        result = ingestor.ingest(
            body.source,
            kind=body.kind,
            title=body.title,
            about=body.about,
            scope=body.scope,
            link_to=body.link_to,
            force_render=body.force_render,
        )
    except FetchRefused as exc:
        raise HTTPException(status_code=400, detail=f"refused: {exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"fetch failed: {exc}") from exc
    except (OcrError, RenderError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except HarnessWriteError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        ingestor.writer.close()
        ingestor.ocr.close()
        ingestor.state.close()
    return IngestOut(
        entity_id=result.entity_id,
        parent_lineage=result.parent_lineage,
        chunks=len(result.chunk_lineages),
        chunk_lineages=result.chunk_lineages,
        route=result.route,
        deduped=result.deduped,
        grounded_to=result.grounded_to,
    )


class GitIndexIn(BaseModel):
    repo: str
    link_to: str | None = None


class GitFetchFileIn(BaseModel):
    repo: str
    path: str
    sha: str | None = None


class GitIngestRepoIn(BaseModel):
    repo: str
    link_to: str | None = None


async def _run_git(fn):  # type: ignore[no-untyped-def]
    'Queue + thread + error mapping shared by the three git endpoints.'
    global _queue_depth
    _queue_depth += 1
    try:
        async with _one_at_a_time:
            return await asyncio.to_thread(fn)
    except GitRefused as exc:
        raise HTTPException(status_code=400, detail=f"refused: {exc}") from exc
    except LlmUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except HarnessWriteError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        _queue_depth -= 1


@app.post("/git/index")
async def git_index(body: GitIndexIn) -> dict:
    def work():  # type: ignore[no-untyped-def]
        svc = build_git_service()
        try:
            return dataclasses.asdict(svc.index_repo(body.repo, link_to=body.link_to))
        finally:
            _close_git(svc)

    return await _run_git(work)  # type: ignore[no-any-return]


@app.post("/git/fetch_file")
async def git_fetch_file(body: GitFetchFileIn) -> dict:
    def work():  # type: ignore[no-untyped-def]
        svc = build_git_service()
        try:
            return svc.fetch_file(body.repo, body.path, body.sha)
        finally:
            _close_git(svc)

    return await _run_git(work)  # type: ignore[no-any-return]


@app.post("/git/ingest_repo")
async def git_ingest_repo(body: GitIngestRepoIn) -> dict:
    def work():  # type: ignore[no-untyped-def]
        svc = build_git_service()
        try:
            return dataclasses.asdict(
                svc.ingest_repo_full(body.repo, link_to=body.link_to)
            )
        finally:
            _close_git(svc)

    return await _run_git(work)  # type: ignore[no-any-return]


class GitActivityIn(BaseModel):
    repo: str
    window: int = 30
    full: bool = False


@app.post("/git/activity")
async def git_activity(body: GitActivityIn) -> dict:
    def work():  # type: ignore[no-untyped-def]
        svc = build_git_service()
        try:
            return dataclasses.asdict(
                svc.commit_activity(body.repo, window=body.window, full=body.full)
            )
        finally:
            _close_git(svc)

    return await _run_git(work)  # type: ignore[no-any-return]


_project_progress: dict[str, dict] = {}
_convo_progress: dict[str, dict] = {}


class ProjectIngestIn(BaseModel):
    root: str
    project: str | None = None
    ignore: list[str] | None = None


class ProjectFetchFileIn(BaseModel):
    project: str
    relpath: str
    max_bytes: int = 200_000


def build_project_ledger() -> ProjectLedger:
    return ProjectLedger(settings.state_db)


def build_conversation_ledger() -> ConversationLedger:
    return ConversationLedger(settings.state_db)


@app.post("/project/ingest")
async def project_ingest(body: ProjectIngestIn) -> dict:
    'Scan a folder and write it into the graph. Serial, resumable, idempotent.'
    def work():  # type: ignore[no-untyped-def]
        ingestor = build_ingestor()
        ledger = build_project_ledger()
        prog = ProjectProgress(project=body.project or Path(body.root).name)
        _project_progress[prog.project] = prog.as_dict()
        try:
            done = ingest_project(
                body.root, body.project, settings=settings, ingestor=ingestor,
                ledger=ledger, ignore=body.ignore, progress=prog,
            )
            return done.as_dict()
        except (FileNotFoundError, NotADirectoryError) as exc:
            prog.error = str(exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            _project_progress[prog.project] = prog.as_dict()
            ingestor.writer.close()
            ingestor.ocr.close()
            ingestor.state.close()
            ledger.close()

    return await _run_git(work)  # type: ignore[no-any-return]


@app.get("/project/status")
async def project_status(project: str | None = None) -> dict:
    'Queue depth + per-project progress (files done/total, current file).'
    return {
        "busy": _one_at_a_time.locked(),
        "queued": max(_queue_depth - 1, 0),
        "projects": (
            {project: _project_progress.get(project)} if project
            else _project_progress
        ),
    }


@app.get("/project/list")
async def project_list() -> dict:
    'Projects that are still IN THE GRAPH, not merely in the ledger.'
    ledger = build_project_ledger()
    writer = HarnessWriter(settings.harness_base, settings.harness_key)
    try:
        live = [name for name, lineage in ledger.project_maps()
                if writer.lineage_is_live(lineage)]
        return {"projects": live}
    finally:
        writer.close()
        ledger.close()


@app.get("/project/nodes")
async def project_nodes(project: str) -> dict:
    ledger = build_project_ledger()
    try:
        known = bool(ledger.paths(project))
        lineages = ledger.content_lineages(project)
    finally:
        ledger.close()
    return {"project": project, "known": known, "lineages": lineages}


@app.post("/project/fetch_file")
async def project_fetch_file(body: ProjectFetchFileIn) -> dict:
    ledger = build_project_ledger()
    try:
        root_row = ledger.get(body.project, "")
        node = ledger.get(body.project, body.relpath)
        if root_row is None:
            raise HTTPException(status_code=404, detail=f"unknown project: {body.project}")
        if node is None:
            raise HTTPException(
                status_code=404,
                detail=f"{body.relpath} is not in project {body.project}")
        if body.relpath == "":
            return {
                "project": body.project, "relpath": "", "path": "",
                "bytes": 0, "binary": False, "content": "",
                "kind": node["kind"], "lineage_id": node["lineage_id"],
            }
        graph_root = None
        for n in ingest_graph_nodes():
            if n.get("lineageId") == root_row["lineage_id"]:
                graph_root = n
                break
        base = ""
        for tag in (graph_root or {}).get("tags", []) or []:
            if str(tag).startswith("root:"):
                base = str(tag)[5:]
        if not base:
            raise HTTPException(status_code=409,
                                detail="project root has no recorded folder")
        target = Path(base) / body.relpath
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"file is gone: {target}")
        raw = target.read_bytes()[: body.max_bytes]
        try:
            text = raw.decode("utf-8")
            binary = False
        except UnicodeDecodeError:
            text = ""
            binary = True
        return {
            "project": body.project, "relpath": body.relpath, "path": str(target),
            "bytes": target.stat().st_size, "binary": binary, "content": text,
            "kind": node["kind"], "lineage_id": node["lineage_id"],
        }
    finally:
        ledger.close()


class ProjectLinkNoteIn(BaseModel):
    note_lineage: str
    project: str
    relpath: str | None = None


@app.post("/project/link_note")
async def project_link_note(body: ProjectLinkNoteIn) -> dict:
    'Join a memory to a project (or one of its files) with `relates_to`.'
    ledger = build_project_ledger()
    w = HarnessWriter(settings.harness_base, settings.harness_key)
    try:
        node = ledger.get(body.project, body.relpath or "")
        if node is None:
            raise HTTPException(
                status_code=404,
                detail=f"{body.relpath or body.project} is not in the graph")
        out = w.link(body.note_lineage, node["lineage_id"], "relates_to")
        return {"note_lineage": body.note_lineage, "project": body.project,
                "relpath": body.relpath or "", "target": node["lineage_id"],
                "link_type": "relates_to", "status": out.get("status")}
    finally:
        w.close()
        ledger.close()


def ingest_graph_nodes() -> list[dict]:
    'The graph read-model, via the harness API (never the DB).'
    w = HarnessWriter(settings.harness_base, settings.harness_key)
    try:
        return list(w.graph(limit=5000).get("nodes", []))
    finally:
        w.close()


class ConversationIngestIn(BaseModel):
    source: str = Field(..., description="hermes | openclaw")
    path: str = Field(..., min_length=1, description="Explicit conversation database or session directory")
    agent: str | None = None
    full: bool = False


@app.post("/conversations/ingest")
async def conversations_ingest(body: ConversationIngestIn) -> dict:
    'Pull new turns from a source into the graph. Resumable, idempotent.'
    if body.source not in CONVERSATION_SOURCES:
        raise HTTPException(status_code=400,
                            detail=f"unknown source {body.source!r}; "
                                   f"expected one of {list(CONVERSATION_SOURCES)}")

    def work():  # type: ignore[no-untyped-def]
        ledger = build_conversation_ledger()
        writer = HarnessWriter(settings.harness_base, settings.harness_key)
        try:
            result = sync_source(
                body.source, path=body.path, agent=body.agent,
                ledger=ledger, writer=writer, scope=settings.artifact_scope,
                full=body.full,
            )
            _convo_progress[body.source] = result
            return result
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            writer.close()
            ledger.close()

    return await _run_git(work)  # type: ignore[no-any-return]


@app.get("/conversations/status")
async def conversations_status(source: str | None = None) -> dict:
    return {
        "queue_depth": max(_queue_depth - 1, 0),
        "busy": _one_at_a_time.locked(),
        "last": _convo_progress.get(source) if source else _convo_progress,
    }


@app.get("/conversations/list")
async def conversations_list(source: str | None = None) -> dict:
    'Every conversation this node has mirrored, from the ledger.'
    ledger = build_conversation_ledger()
    try:
        return {"conversations": ledger.sessions(source)}
    finally:
        ledger.close()


class SeedRecordsIn(BaseModel):
    chapter: str = Field(..., description="the chapter these came from, e.g. '05'")
    records: list[dict] = Field(default_factory=list)
    approved: bool = False
    dry_run: bool = False


def _require_seed_key(authorization: str = Header(default="")) -> None:
    'Fail closed. No key configured means the route is shut, not open.'
    expected = settings.seed_api_key
    token = authorization.removeprefix("Bearer ").strip()
    if not expected or not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid or missing seed key")


@app.post("/seed/records", dependencies=[Depends(_require_seed_key)])
async def seed_records(body: SeedRecordsIn) -> dict:

    def work():  # type: ignore[no-untyped-def]
        writer = MemoryWriter(settings.harness_base,
                              settings.seed_key or settings.harness_key)
        ledger = SeedLedger(settings.seed_ledger)
        hub_writer = HubWriter(
            HarnessWriter(settings.harness_base, settings.harness_key),
            settings.artifact_scope)
        try:
            catalog = writer.emotions()
            matcher = _emotion_matcher(catalog)
            return ingest_records(
                body.model_dump(), writer=writer, ledger=ledger,
                hub_writer=hub_writer, catalog=catalog, matcher=matcher,
                existing_hub=_existing_hub(body.chapter),
            ).as_dict()
        except NotApproved as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            writer.close()
            ledger.close()

    return await _run_git(work)  # type: ignore[no-any-return]


def _emotion_matcher(catalog: list[dict]):  # type: ignore[no-untyped-def]
    "The Fixes-08 matcher, or None when the nodes aren't configured."
    if not settings.embed_key:
        log.warning("no EMBED_KEY — emotions will be exact-match only")
        return None
    try:
        from memory_ingest.seed.emotions import ModelChooser, build_matcher
        from memory_ingest.seed.model import SeedModel

        chooser = ModelChooser(SeedModel(settings)) if settings.seed_model_key else None
        return build_matcher(catalog, base_url=settings.embed_base,
                             api_key=settings.embed_key, chooser=chooser,
                             cache_path=Path(settings.emotion_cache).expanduser(),
                             floor=settings.emotion_floor)
    except Exception as exc:  # noqa: BLE001 - never lose a submission to this
        log.warning("emotion matcher unavailable: %s", exc)
        return None


def _existing_hub(chapter: str) -> str | None:
    "The chapter's hub, so a re-send revises it instead of minting a second."
    import sqlite3

    try:
        con = sqlite3.connect(
            Path(settings.harness_db).resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = con.execute(
            "SELECT lineage_id FROM notes WHERE is_head=1 AND status='active'"
            " AND tags LIKE ? AND tags LIKE ? LIMIT 1",
            ("%episodic-memory-hub%", f"%chapter:{chapter}%"),
        ).fetchone()
        return str(row[0]) if row else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def build_book_ledger() -> BookLedger:
    return BookLedger(settings.state_db)


_library_runs: dict[str, BookProgress] = {}


async def _ingest_book_task(stored, *, title, author, published, synopsis,
                            slug, prog) -> None:  # type: ignore[no-untyped-def]
    'The actual import, off the request. Survives the client hanging up.'
    def work():  # type: ignore[no-untyped-def]
        writer = HarnessWriter(settings.harness_base, settings.harness_key)
        ledger = build_book_ledger()
        ocr = OcrClient(settings.ocr_base, settings.ocr_key) if settings.ocr_key else None
        try:
            return ingest_book(
                stored, title=title, author=author, published=published,
                synopsis=synopsis, slug=slug, writer=writer, ledger=ledger,
                ocr_pages=(lambda images: ocr.ocr_pages(images)) if ocr else None,
                rasterize_page=rasterize_page, progress=prog,
            ).as_dict()
        finally:
            writer.close()
            ledger.close()
            if ocr:
                ocr.close()

    try:
        await _run_git(work)
    except HTTPException as exc:
        prog.error = str(exc.detail)
        prog.finished = True
    except Exception as exc:  # noqa: BLE001 - a background task must not vanish
        log.exception("book %s failed", prog.slug)
        prog.error = str(exc)
        prog.finished = True
    else:
        prog.finished = True


@app.post("/library/upload", status_code=202,
          dependencies=[Depends(_require_seed_key)])
async def library_upload(
    file: UploadFile = File(...),
    title: str = Form(...),
    author: str | None = Form(None),
    published: str | None = Form(None),
    synopsis: str | None = Form(None),
    slug: str | None = Form(None),
) -> dict:
    'Accept a PDF and start importing it. Returns as soon as the file is safe.'
    tmp = Path(tempfile.mkdtemp(prefix="book-")) / "upload.pdf"
    try:
        with tmp.open("wb") as fh:
            while block := await file.read(1 << 20):
                fh.write(block)
        stored = store_upload(tmp, settings)
    except BookRefused as exc:
        shutil.rmtree(tmp.parent, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()

    book = slug or slugify_title(title)
    prog = BookProgress(slug=book, title=title)
    _library_runs[book] = prog
    asyncio.create_task(_ingest_book_task(
        stored, title=title, author=author, published=published,
        synopsis=synopsis, slug=slug, prog=prog))

    return {
        "book": book,
        "title": title,
        "status": "queued",
        "queue_depth": max(_queue_depth - 1, 0),
        "poll": f"/library/status?book={book}",
        "note": ("importing in the background — poll `poll` until finished is "
                 "true. A scanned book is minutes to hours; a digital one is "
                 "usually seconds."),
    }


@app.get("/library/status", dependencies=[Depends(_require_seed_key)])
async def library_status(book: str | None = None) -> dict:
    'How far along an import is. Poll this after an upload.'
    runs = {k: v.as_dict() for k, v in _library_runs.items()}
    return {
        "queue_depth": max(_queue_depth - 1, 0),
        "busy": _one_at_a_time.locked(),
        "book": runs.get(book) if book else runs,
    }


@app.get("/library/list", dependencies=[Depends(_require_seed_key)])
async def library_list() -> dict:
    'Every book this node has imported, from the ledger.'
    ledger = build_book_ledger()
    try:
        return {"books": ledger.books()}
    finally:
        ledger.close()


def build_journal_ledger() -> JournalLedger:
    return JournalLedger(settings.state_db)


def _journal_emotion_resolver():  # type: ignore[no-untyped-def]
    '`emotional_context` → one of the 127, or None to stay Neutral.'
    writer = MemoryWriter(settings.harness_base,
                          settings.seed_key or settings.harness_key)
    try:
        matcher = _emotion_matcher(writer.emotions())
    except Exception as exc:  # noqa: BLE001 - never lose an entry to this
        log.warning("journal emotion matcher unavailable: %s", exc)
        return None
    finally:
        writer.close()
    if matcher is None:
        return None

    def resolve(text: str) -> str | None:
        match = matcher.resolve_many([text]).get(text)
        return match.name if match is not None else None

    return resolve


@app.post("/journal/upsert", dependencies=[Depends(_require_seed_key)])
async def journal_upsert(payload: dict) -> dict:
    'Mirror one journal entry. Idempotent on `journal:{date}:{type}`.'
    try:
        entry = parse_journal(payload)
    except JournalRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def work() -> dict:
        writer = HarnessWriter(settings.harness_base, settings.harness_key)
        ledger = build_journal_ledger()
        try:
            jw = JournalWriter(writer, ledger, _journal_emotion_resolver())
            return jw.write(entry).as_dict()
        finally:
            writer.close()
            ledger.close()

    return await _run_git(work)


@app.get("/journal/list", dependencies=[Depends(_require_seed_key)])
async def journal_list() -> dict:
    ledger = build_journal_ledger()
    try:
        return {"entries": ledger.entries()}
    finally:
        ledger.close()


def _blog_emotion_resolver():  # type: ignore[no-untyped-def]
    return _journal_emotion_resolver()


def build_blog_ledger() -> BlogLedger:
    return BlogLedger(settings.state_db)


@app.post("/blog/upsert", dependencies=[Depends(_require_seed_key)])
async def blog_upsert(payload: dict) -> dict:
    try:
        post = parse_blog(payload)
    except BlogRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def work() -> dict:
        if not post.body:
            try:
                source = fetch_url(post.url, settings)
                post.body = parse_html(source.data).text
                log.info("blog %s: body fetched from %s (%d chars)",
                         post.guid, post.url, len(post.body))
            except Exception as exc:  # noqa: BLE001 - a bodyless post still lands
                log.warning("blog %s: body fetch failed (%s); storing without it",
                            post.guid, exc)

        writer = HarnessWriter(settings.harness_base, settings.harness_key)
        ledger = build_blog_ledger()
        try:
            bw = BlogWriter(
                writer, ledger,
                artifact_scope=settings.artifact_scope,
                emotion_resolver=_blog_emotion_resolver(),
                chunk_tokens=settings.chunk_tokens,
                chunk_overlap_tokens=settings.chunk_overlap_tokens,
            )
            return bw.write(post).as_dict()
        finally:
            writer.close()
            ledger.close()

    out = await _run_git(work)
    log.info("blog_upsert guid=%s minted=%s revised=%s",
             post.guid, out.get("minted"), out.get("revised"))
    return out


@app.get("/blog/list", dependencies=[Depends(_require_seed_key)])
async def blog_list() -> dict:
    ledger = build_blog_ledger()
    try:
        return {"posts": ledger.entries()}
    finally:
        ledger.close()


class BlogBackfillIn(BaseModel):
    'Defaults are deliberately the safe ones: report, do not write.'

    dry_run: bool = True
    limit: int = 20
    feed_url: str = ""


@app.post("/blog/backfill", dependencies=[Depends(_require_seed_key)])
async def blog_backfill(body: BlogBackfillIn | None = None) -> dict:
    'Read the feed, find posts the ledger has never seen, and store them.'
    req = body or BlogBackfillIn()
    feed_url = req.feed_url or settings.blog_feed_url
    if not feed_url:
        raise HTTPException(status_code=400, detail="Set BLOG_FEED_URL or supply feed_url")

    def work() -> dict:
        try:
            source = fetch_url(feed_url, settings)
            items = parse_feed(source.data)
        except FeedRefused as exc:
            raise HTTPException(status_code=400, detail=f"feed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502, detail=f"feed unreachable: {exc}") from exc

        ledger = build_blog_ledger()
        try:
            known = {str(e["guid"]) for e in ledger.entries()}
        finally:
            ledger.close()

        pending = missing_from(items, known)[: max(0, req.limit)]
        planned = [{"guid": i.guid, "title": i.title, "date": i.published_at[:10],
                    "url": i.url} for i in pending]

        if req.dry_run:
            return {"dry_run": True, "in_feed": len(items),
                    "already_stored": len(known), "would_write": planned,
                    "written": []}

        writer = HarnessWriter(settings.harness_base, settings.harness_key)
        ledger = build_blog_ledger()
        written: list[dict] = []
        failed: list[dict] = []
        try:
            bw = BlogWriter(
                writer, ledger,
                artifact_scope=settings.artifact_scope,
                emotion_resolver=_blog_emotion_resolver(),
                chunk_tokens=settings.chunk_tokens,
                chunk_overlap_tokens=settings.chunk_overlap_tokens,
            )
            for item in pending:
                try:
                    post = parse_blog(item.as_payload())
                    got = fetch_url(post.url, settings)
                    post.body = parse_html(got.data).text
                    written.append(bw.write(post).as_dict())
                except Exception as exc:  # noqa: BLE001 - one bad post must not
                    log.warning("backfill %s failed: %s", item.guid, exc)
                    failed.append({"guid": item.guid, "error": str(exc)[:200]})
        finally:
            writer.close()
            ledger.close()

        return {"dry_run": False, "in_feed": len(items),
                "already_stored": len(known), "would_write": planned,
                "written": written, "failed": failed}

    return await _run_git(work)


def build_todo_ledger() -> TodoLedger:
    return TodoLedger(settings.state_db)


@app.post("/todo/upsert", dependencies=[Depends(_require_seed_key)])
async def todo_upsert(payload: dict) -> dict:
    try:
        day = parse_todo_day(payload)
    except TodoRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def work() -> dict:
        writer = HarnessWriter(settings.harness_base, settings.harness_key)
        ledger = build_todo_ledger()
        try:
            return TodoWriter(writer, ledger).write(day).as_dict()
        finally:
            writer.close()
            ledger.close()

    return await _run_git(work)


@app.get("/todo/list", dependencies=[Depends(_require_seed_key)])
async def todo_list() -> dict:
    ledger = build_todo_ledger()
    try:
        return {"entries": ledger.entries()}
    finally:
        ledger.close()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
