from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from memory_harness.api.routes import router
from memory_harness.clients.embeddings import EmbeddingsClient
from memory_harness.clients.llm import LlmClient
from memory_harness.config import Settings, get_settings
from memory_harness.content_store import ContentStore
from memory_harness.db.connection import open_writer
from memory_harness.db.migrate import apply_migrations, schema_version
from memory_harness.db.writer import WriteQueue
from memory_harness.domain.emotions import UnknownEmotion, seed_emotions
from memory_harness.domain.identity import seed_identities
from memory_harness.domain.scope import Forbidden
from memory_harness.obs import metrics
from memory_harness.obs.fleet_collector import build_collector
from memory_harness.obs.logging import configure_logging, get_logger
from memory_harness.workers.decay_worker import decay_worker_loop
from memory_harness.workers.embed_worker import embed_worker_loop
from memory_harness.workers.extract_worker import extract_worker_loop
from memory_fleet import from_env as fleet_from_env, start as fleet_start


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        log = get_logger("main")
        con = open_writer(settings.db.path, settings.db.busy_timeout_ms)
        applied = apply_migrations(con)
        seeded = seed_emotions(con)
        identities_seeded = seed_identities(con)
        wq = WriteQueue(con)
        app.state.settings = settings
        app.state.writer = wq
        app.state.content_store = ContentStore(settings.db.blob_dir)
        app.state.embeddings = EmbeddingsClient(
            settings.embeddings.base_url,
            settings.embeddings.api_key,
            timeout_s=settings.embeddings.timeout_s,
            dim=settings.embeddings.dim,
            max_batch=settings.embeddings.max_batch,
        )
        app.state.workers = []
        app.state.worker_names = []
        if settings.embeddings.api_key:
            app.state.workers.append(
                asyncio.create_task(
                    embed_worker_loop(
                        wq, app.state.content_store, app.state.embeddings
                    )
                )
            )
            app.state.worker_names.append("embed_worker")
        if settings.llm.api_key:
            app.state.llm = LlmClient(
                settings.llm.base_url,
                settings.llm.model,
                settings.llm.api_key,
                max_tokens=settings.llm.max_tokens,
                timeout_s=settings.llm.timeout_s,
            )
            app.state.workers.append(
                asyncio.create_task(
                    extract_worker_loop(wq, app.state.content_store, app.state.llm)
                )
            )
            app.state.worker_names.append("extract_worker")
        app.state.workers.append(asyncio.create_task(decay_worker_loop(wq)))
        app.state.worker_names.append("decay_worker")

        app.state.fleet = None
        app.state.fleet_task = None
        fleet_cfg = fleet_from_env("harness")
        if fleet_cfg is not None:
            collector = build_collector(
                settings, app.state.workers, tuple(app.state.worker_names)
            )
            app.state.fleet, app.state.fleet_task = fleet_start(fleet_cfg, collector)
            log.info(
                "fleet publisher started",
                extra={"node_id": fleet_cfg.node_id, "source_id": fleet_cfg.source_id},
            )
        log.info(
            "harness started",
            extra={
                "schema_version": schema_version(con),
                "migrations_applied": applied,
                "emotions_seeded": seeded,
                "identities_seeded": identities_seeded,
                "db_path": settings.db.path,
            },
        )
        yield
        if app.state.fleet is not None:
            await app.state.fleet.stop("harness shutdown")
            await asyncio.sleep(0.2)
            app.state.fleet_task.cancel()
        for task in app.state.workers:
            task.cancel()
        wq.close()
        log.info("harness stopped")

    app = FastAPI(docs_url=None, redoc_url=None, title="Memory-harness", version="0.1.0", lifespan=lifespan)

    from fastapi.middleware.gzip import GZipMiddleware

    app.add_middleware(GZipMiddleware, minimum_size=1024)

    if settings.server.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.server.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )
    app.include_router(router)
    from memory_harness.api.stream import router as stream_router

    app.include_router(stream_router)

    @app.exception_handler(Forbidden)
    async def forbidden_handler(request: Request, exc: Forbidden) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": f"forbidden: {exc}"})

    @app.exception_handler(UnknownEmotion)
    async def unknown_emotion_handler(
        request: Request, exc: UnknownEmotion
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400, content={"detail": f"unknown emotion: {exc}"}
        )

    @app.get("/metrics", include_in_schema=False)
    async def metrics_route() -> Response:
        body, content_type = metrics.render()
        return Response(content=body, media_type=content_type)

    return app


app = create_app()


def run() -> None:
    'Console entry point: `memory-harness` (single uvicorn worker).'
    settings = get_settings()
    uvicorn.run(
        "memory_harness.main:app",
        host=settings.server.host,
        port=settings.server.port,
        workers=1,
    )
