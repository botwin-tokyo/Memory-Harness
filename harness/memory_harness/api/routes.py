'The API endpoints, thin: parse -> domain -> respond. Steps 0.7+.'

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from memory_harness.auth.deps import require_principal
from memory_harness.db.connection import open_reader
from memory_harness.db.migrate import schema_version
from memory_harness.domain import adjudication
from memory_harness.domain import ask as ask_mod
from memory_harness.domain import askevent
from memory_harness.domain import conversations as conversations_mod
from memory_harness.domain import emotions as emotions_mod
from memory_harness.domain import private_memory as private_mod
from memory_harness.domain import secrets_scan
from memory_harness.domain import propose as propose_mod
from memory_harness.domain import changes as changes_mod
from memory_harness.domain import events
from memory_harness.domain import core_memory
from memory_harness.domain import forget as forget_mod
from memory_harness.domain import graph as graph_mod
from memory_harness.domain import library as library_mod
from memory_harness.domain import scope as scope_mod
from memory_harness.domain import history as history_mod
from memory_harness.domain import identity as identity_mod
from memory_harness.domain import intake as intake_mod
from memory_harness.domain import links as links_mod
from memory_harness.domain import procedures as procedures_mod
from memory_harness.domain import recall as recall_mod
from memory_harness.domain import source as source_mod
from memory_harness.domain import remember as remember_mod
from memory_harness.clients.llm import LlmUnavailable
from memory_harness.domain.emotions import resolve_emotion
from memory_harness.domain.scope import Forbidden, default_write_scope, visible_scopes
from memory_harness.domain.trust import assign_source_trust
from memory_harness.models.api import (
    AskIn,
    AskOut,
    ChangesOut,
    CoreIn,
    CoreOut,
    DisputeQueueOut,
    EmotionOut,
    EntityIn,
    EntityOut,
    EventIn,
    EventOut,
    ExploreIn,
    ExploreItem,
    ExploreOut,
    ForgetIn,
    LibrarySearchIn,
    ForgetOut,
    GraphSnapshotOut,
    GraphSnapshotMinimalOut,
    GraphDeltaOut,
    HistoryOut,
    IntakeIn,
    IntakeOut,
    LinkIn,
    LinkOut,
    RecallIn,
    RecallOut,
    RememberIn,
    RememberOut,
    ResolveIn,
    ResolveOut,
    SourceChunk,
    SourceCitation,
    SourceIn,
    SourceOut,
    SearchIn,
    SearchOut,
    TypesOut,
    WhoAmIOut,
)
from memory_harness.domain import search as search_mod
from memory_harness.models.domain import MEMORY_TYPES, Principal
from memory_harness.obs import metrics
from memory_harness.obs.logging import get_logger
from memory_harness.time import monotonic_ms

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, object]:
    'Liveness + schema/emotions sanity. Unauthenticated (load-balancer probe).'
    settings = request.app.state.settings
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        version = schema_version(reader)
        emotions = int(reader.execute("SELECT COUNT(*) FROM emotions").fetchone()[0])
    finally:
        reader.close()
    return {"status": "ok", "schema_version": version, "emotions": emotions}


@router.get("/v1/types", response_model=TypesOut)
async def list_types(principal: Principal = Depends(require_principal)) -> TypesOut:
    'Which memory types this harness accepts.'
    return TypesOut(types=list(MEMORY_TYPES))


@router.post("/v1/search", response_model=SearchOut)
async def post_search(
    q: SearchIn, request: Request, principal: Principal = Depends(require_principal)
) -> SearchOut:
    'Semantic search over memories: a flat ranked list, no answer generation.'
    settings = request.app.state.settings
    qvec = await asyncio.to_thread(
        request.app.state.embeddings.embed_query, q.query
    )
    if qvec is None:
        raise HTTPException(status_code=503, detail="Semantic search unavailable: embedding service unavailable")
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        if reader.execute("SELECT 1 FROM sqlite_master WHERE name='vec' AND type='table'").fetchone() is None:
            raise HTTPException(status_code=503, detail="Semantic search unavailable: vector index not initialized")
        out = await asyncio.to_thread(
            search_mod.search, reader, principal,
            query=q.query, qvec=qvec, types=q.types,
            tags_all=q.tags_all, tags_any=q.tags_any, limit=q.limit,
        )
    finally:
        reader.close()
    get_logger("api").info(
        "search",
        extra={"principal_id": principal.principal_id, "route": "POST /v1/search",
               "types": q.types, "hits": out["count"], "embedded": qvec is not None},
    )
    return SearchOut(**out)


@router.get("/v1/emotions", response_model=list[EmotionOut])
async def list_emotions(
    request: Request, principal: Principal = Depends(require_principal)
) -> JSONResponse:
    'The 127-emotion reference vocabulary (D50). Static/cacheable.'
    settings = request.app.state.settings
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        rows = reader.execute(
            "SELECT emotion_id, name, family, description FROM emotions ORDER BY emotion_id"
        ).fetchall()
    finally:
        reader.close()
    body = [
        EmotionOut(
            emotion_id=int(r["emotion_id"]),
            name=str(r["name"]),
            family=str(r["family"]),
            description=str(r["description"]),
        )
        for r in rows
    ]
    return JSONResponse(
        content=[e.model_dump() for e in body],
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@router.get("/v1/whoami", response_model=WhoAmIOut)
async def whoami(principal: Principal = Depends(require_principal)) -> WhoAmIOut:
    'Echo the derived identity (the cheapest test that key-as-identity works).'
    return WhoAmIOut(
        principal_id=principal.principal_id,
        node=principal.node,
        trust_ceiling=principal.trust_ceiling,
        allowed_scopes=principal.allowed_scopes,
        home_scope=principal.home_scope,
        learning_eligible_default=principal.learning_eligible_default,
        secret_mode=principal.secret_mode,
        link_authority=principal.link_authority,
    )


@router.post("/v1/entities", response_model=EntityOut)
async def post_entity(
    e: EntityIn, request: Request, principal: Principal = Depends(require_principal)
) -> EntityOut:
    'Register a `<kind>:<slug>` registry entity + aliases (Grounded Memory).'
    try:
        eid = await request.app.state.writer.tx(
            lambda cur: identity_mod.register_entity(
                cur,
                e.kind,
                e.name,
                slug=e.slug,
                pronouns=e.pronouns,
                description=e.description,
                aliases=e.aliases,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EntityOut(
        id=eid,
        display=e.name.strip(),
        subject_class="person" if e.kind == "person" else "world",
    )


@router.post("/v1/events", response_model=EventOut)
async def post_event(
    ev: EventIn, request: Request, principal: Principal = Depends(require_principal)
) -> EventOut:
    if ev.event_type not in events.CAPTURE_EVENT_TYPES:
        raise HTTPException(
            status_code=400, detail=f"unknown capture event_type: {ev.event_type}"
        )
    settings = request.app.state.settings
    scope = default_write_scope(principal, ev.scope)
    source_trust = assign_source_trust(principal.trust_ceiling, ev.source_class)
    emotion_id = 127
    if ev.emotion is not None:
        reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
        try:
            emotion_id = resolve_emotion(reader, ev.emotion)
        finally:
            reader.close()

    started = monotonic_ms()
    result = await events.append_event(
        request.app.state.writer,
        request.app.state.content_store,
        ev,
        principal,
        scope=scope,
        source_trust=source_trust,
        emotion_id=emotion_id,
        inline_body_max_bytes=settings.db.inline_body_max_bytes,
        entropy_threshold=settings.secrets.entropy_threshold,
    )
    metrics.append_latency_ms.observe(monotonic_ms() - started)
    if not result.deduped:
        metrics.events_appended_total.inc()
    if result.redacted:
        metrics.secrets_redacted_total.inc()
    get_logger("api").info(
        "event appended",
        extra={
            "principal_id": principal.principal_id,
            "route": "POST /v1/events",
            "latency_ms": monotonic_ms() - started,
            "deduped": result.deduped,
            "redacted": result.redacted,
        },
    )
    return EventOut(
        event_id=result.event_id,
        deduped=result.deduped,
        embedding="queued",
        redacted=result.redacted,
    )


@router.post("/v1/remember", response_model=RememberOut)
async def post_remember(
    r: RememberIn, request: Request, principal: Principal = Depends(require_principal)
) -> RememberOut:
    'Low-level durable write: synchronous, trust-gated, emotion required (D50).'
    if r.type not in MEMORY_TYPES:
        raise HTTPException(status_code=400, detail=f"unknown memory type: {r.type}")
    if not r.emotion:
        raise HTTPException(status_code=400, detail="emotion is required (D50)")
    settings = request.app.state.settings
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        emotion_id = resolve_emotion(reader, r.emotion)
    finally:
        reader.close()
    result = await remember_mod.remember(
        request.app.state.writer,
        principal,
        r,
        emotion_id=emotion_id,
        entropy_threshold=settings.secrets.entropy_threshold,
    )
    metrics.writes_total.labels(route="remember").inc()
    if result.disputed:
        metrics.disputes_total.inc()
    get_logger("api").info(
        "remember",
        extra={
            "principal_id": principal.principal_id,
            "route": "POST /v1/remember",
            "type": r.type,
            "ref": result.ref,
            "disputed": result.disputed,
        },
    )
    return RememberOut(
        lineage_id=result.lineage_id,
        revision=result.revision,
        ref=result.ref,
        superseded=result.superseded_id,
    )


@router.post("/v1/core", response_model=CoreOut)
async def post_core(
    c: CoreIn, request: Request, principal: Principal = Depends(require_principal)
) -> CoreOut:
    if c.op in ("append", "replace") and not c.value:
        raise HTTPException(status_code=400, detail=f"value is required for {c.op}")
    settings = request.app.state.settings
    try:
        out = await core_memory.edit_core(
            request.app.state.writer,
            principal,
            c,
            shared_budget=settings.core.shared_budget_tokens,
            agent_budget=settings.core.agent_budget_tokens,
        )
    except core_memory.CoreNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    metrics.writes_total.labels(route="core").inc()
    return out


@router.post("/v1/source", response_model=SourceOut)
async def post_source(
    q: SourceIn, request: Request, principal: Principal = Depends(require_principal)
) -> SourceOut:
    settings = request.app.state.settings
    qvec = None
    if q.query:
        qvec = await asyncio.to_thread(
            request.app.state.embeddings.embed_query, q.query
        )
        if qvec is None:
            get_logger("api").warning(
                "source search degraded: embeddings unavailable (keyword-only)",
                extra={"principal_id": principal.principal_id},
            )
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        res = source_mod.gather_source(
            reader, principal, q.target,
            query=q.query, offset=q.offset, limit=q.limit, window=q.window,
            token_budget=q.token_budget, qvec=qvec,
        )
    except links_mod.LineageNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    finally:
        reader.close()
    return SourceOut(
        source=SourceCitation(**vars(res.source)),
        grounded_memory=res.grounded_memory,
        mode=res.mode,  # type: ignore[arg-type]
        chunks=[
            SourceChunk(index=c.index, text=c.text, relevance=c.relevance)
            for c in res.chunks
        ],
        total_chunks=res.total_chunks,
        returned=len(res.chunks),
        truncated=res.truncated,
        token_estimate=res.token_estimate,
        note=res.note,
    )


@router.post("/v1/recall", response_model=RecallOut)
async def post_recall(
    q: RecallIn, request: Request, principal: Principal = Depends(require_principal)
) -> RecallOut:
    settings = request.app.state.settings
    started = monotonic_ms()
    embed_started = monotonic_ms()
    qvec = await asyncio.to_thread(
        request.app.state.embeddings.embed_query, q.query
    )
    metrics.embed_latency_ms.observe(monotonic_ms() - embed_started)
    if qvec is None:
        get_logger("api").warning(
            "recall degraded: embeddings unavailable (keyword-only)",
            extra={"principal_id": principal.principal_id},
        )
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        out = recall_mod.recall(reader, principal, q, settings, qvec)
    except scope_mod.Forbidden as e:
        raise HTTPException(status_code=403, detail=f"no access to {e}") from e
    finally:
        reader.close()
    metrics.recall_latency_ms.observe(monotonic_ms() - started)
    get_logger("api").info(
        "recall",
        extra={
            "principal_id": principal.principal_id,
            "route": "POST /v1/recall",
            "latency_ms": monotonic_ms() - started,
            "facts": len(out.facts),
            "episodes": len(out.episodes),
        },
    )
    return out


@router.get("/v1/changes", response_model=ChangesOut)
async def get_changes(
    request: Request,
    principal: Principal = Depends(require_principal),
    since: int = 0,
    scope: str | None = None,
    types: str | None = None,
    learning_eligible: bool | None = None,
    limit: int = 500,
) -> ChangesOut:
    settings = request.app.state.settings
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        if learning_eligible is True and principal.trust_ceiling != "user":
            from memory_harness.db import meta as meta_mod

            cur = reader.cursor()
            if meta_mod.get(cur, "secret_scan_verified") != "1":
                raise HTTPException(
                    status_code=403,
                    detail="training lane closed: secret scan not verified"
                    " (run scripts/verify_secret_gate.py first)",
                )
        return changes_mod.changes(
            reader,
            principal,
            since=since,
            scope=scope,
            types=[t for t in (types or "").split(",") if t] or None,
            learning_eligible=learning_eligible,
            limit=min(limit, 1000),
        )
    finally:
        reader.close()


@router.post("/v1/ask", response_model=AskOut)
async def post_ask(
    a: AskIn, request: Request, principal: Principal = Depends(require_principal)
) -> AskOut:
    settings = request.app.state.settings
    started_ms = monotonic_ms()
    node = principal.node
    await request.app.state.writer.tx(
        lambda cur: askevent.emit_ask_started(
            cur, question=a.text, asked_by=principal.principal_id, node=node,
            client_ask_id=a.client_ask_id,
            entropy_threshold=settings.secrets.entropy_threshold,
        )
    )
    qvec = await asyncio.to_thread(request.app.state.embeddings.embed_query, a.text)
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        out = await asyncio.to_thread(
            ask_mod.ask, reader, principal, settings,
            text=a.text, qvec=qvec, token_budget=a.token_budget,
            embed=request.app.state.embeddings.embed_query,
        )
    except LlmUnavailable as e:
        raise HTTPException(status_code=503, detail=f"model service unavailable: {e}") from e
    finally:
        reader.close()

    await request.app.state.writer.tx(
        lambda cur: askevent.emit_ask_answered(
            cur, question=a.text, answer=str(out["answer"]),
            mood=str(out["mood"]), mood_family=str(out["mood_family"]),
            sources=[str(x) for x in list(out["sources"])],  # type: ignore[call-overload]
            tools_used=[str(x) for x in list(out.get("tools_used") or [])],  # type: ignore[call-overload]
            asked_by=principal.principal_id, node=node,
            client_ask_id=a.client_ask_id,
            elapsed_ms=monotonic_ms() - started_ms,
            entropy_threshold=settings.secrets.entropy_threshold,
        )
    )
    get_logger("api").info(
        "ask",
        extra={"principal_id": principal.principal_id, "route": "POST /v1/ask",
               "mood": out["mood_family"], "sources": len(list(out["sources"])),  # type: ignore[call-overload]
               "tools": out.get("tools_used")},
    )
    return AskOut.model_validate(out)


@router.post("/v1/forget", response_model=ForgetOut)
async def post_forget(
    f: ForgetIn, request: Request, principal: Principal = Depends(require_principal)
) -> ForgetOut:
    if not f.id and not f.selector:
        raise HTTPException(status_code=400, detail="id or selector is required")
    n = await forget_mod.forget(request.app.state.writer, principal, f)
    metrics.writes_total.labels(route="forget").inc()
    get_logger("api").info(
        "forget",
        extra={"principal_id": principal.principal_id, "route": "POST /v1/forget",
               "retracted": n, "reason": f.reason},
    )
    return ForgetOut(retracted=n)


@router.get("/v1/history/{lineage_id}", response_model=HistoryOut)
async def get_history(
    lineage_id: str, request: Request, principal: Principal = Depends(require_principal)
) -> HistoryOut:
    'Full revision chain of a memory, oldest → newest, HEAD flagged.'
    settings = request.app.state.settings
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        mtype, revisions = history_mod.history(reader, principal, lineage_id)
    except history_mod.LineageNotFound as e:
        raise HTTPException(status_code=404, detail=f"lineage not found: {e}") from e
    finally:
        reader.close()
    return HistoryOut(lineage_id=lineage_id, type=mtype, revisions=revisions)


@router.post("/v1/link", response_model=LinkOut)
async def post_link(
    l: LinkIn, request: Request, principal: Principal = Depends(require_principal)
) -> LinkOut:
    if l.action is not None or l.link_id is not None:
        if not l.link_id or l.action not in ("promote", "reject", "retract"):
            raise HTTPException(
                status_code=400,
                detail="transition needs link_id + action promote|reject|retract",
            )
        if principal.link_authority != "active" and principal.trust_ceiling not in (
            "user",
            "trusted_tool",
        ):
            raise HTTPException(status_code=403, detail="link transitions need curator authority")
        try:
            out = await request.app.state.writer.tx(
                lambda cur: links_mod.transition_link(
                    cur,
                    l.link_id,
                    l.action,
                    curated_by=principal.principal_id,
                    source_trust=principal.trust_ceiling,
                    created_on_node=principal.node,
                    learning_eligible=principal.learning_eligible_default,
                )
            )
        except links_mod.LineageNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return LinkOut(**out)

    if not l.src_lineage_id or not l.dst_lineage_id or not l.link_type:
        raise HTTPException(status_code=400, detail="src/dst/link_type are required")
    try:
        link_type = links_mod.validate_link_type(l.link_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    status = "active" if principal.link_authority == "active" else "proposed"

    def txn(cur):  # type: ignore[no-untyped-def]
        _tbl, src_row = links_mod.require_visible(principal, cur, l.src_lineage_id)
        links_mod.require_visible(principal, cur, l.dst_lineage_id)
        return links_mod.create_link(
            cur,
            l.src_lineage_id,
            l.dst_lineage_id,
            link_type,
            status,
            proposed_by=principal.principal_id,
            curated_by=principal.principal_id if status == "active" else None,
            scope=str(src_row["scope"]),
            weight=l.weight,
            source_trust=principal.trust_ceiling,
            created_on_node=principal.node,
            learning_eligible=principal.learning_eligible_default,
        )

    try:
        out = await request.app.state.writer.tx(txn)
    except links_mod.LineageNotFound as e:
        raise HTTPException(status_code=404, detail=f"lineage not found: {e}") from e
    metrics.writes_total.labels(route="link").inc()
    return LinkOut(
        link_id=out["link_id"],
        link_type=out["link_type"],
        target=out["target"],
        status=out["status"],
    )


_TABLE_KIND = {
    "facts": ("fact", "fact_id", "gloss"),
    "notes": ("note", "note_id", "body"),
    "episodes": ("episode", "episode_id", "summary"),
    "preferences": ("preference", "preference_id", "statement"),
    "procedures": ("procedure", "procedure_id", "steps_md"),
}


@router.post("/v1/explore", response_model=ExploreOut)
async def post_explore(
    e: ExploreIn, request: Request, principal: Principal = Depends(require_principal)
) -> ExploreOut:
    settings = request.app.state.settings
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        rows = graph_mod.explore(
            reader, principal, e.start_id, e.depth,
            link_types=e.link_types, max_depth=settings.graph.explore_max_depth,
            direction=e.direction,
        )
        items: list[ExploreItem] = []
        total = 0
        for table, row, hop in rows:
            kind, pk, text_col = _TABLE_KIND[table]
            text = str(row[text_col])
            status = str(row["status"]) if "status" in row.keys() else "active"
            confidence = float(row["confidence"])
            usage = recall_mod._usage(status, confidence, str(row["source_trust"]))
            if str(row["source_trust"]) == "untrusted":
                text = recall_mod.UNTRUSTED_FENCE + text
            toks = core_memory.estimate_tokens(text)
            if total + toks > e.budget:
                continue
            total += toks
            items.append(
                ExploreItem(
                    kind=kind,
                    id=str(row[pk]),
                    lineage_id=str(row["lineage_id"]),
                    revision=int(row["revision"]),
                    hop=hop,
                    text=text,
                    source_trust=str(row["source_trust"]),
                    scope=str(row["scope"]),
                    usage=usage,
                    links_out=graph_mod.links_out_map(
                        reader, str(row["lineage_id"]), visible_scopes(principal)
                    ),
                )
            )
    except links_mod.LineageNotFound as e2:
        raise HTTPException(status_code=404, detail=f"lineage not found: {e2}") from e2
    finally:
        reader.close()
    return ExploreOut(
        start_id=e.start_id,
        depth=min(e.depth, settings.graph.explore_max_depth),
        items=items,
        token_estimate=total,
    )


@router.post("/v1/intake", response_model=IntakeOut)
async def post_intake(
    form: IntakeIn, request: Request, principal: Principal = Depends(require_principal)
) -> IntakeOut:
    'The memory intake form: one filled record + its branches, atomically.'
    if form.type not in MEMORY_TYPES:
        raise HTTPException(status_code=400, detail=f"unknown memory type: {form.type}")
    if not form.emotion:
        raise HTTPException(status_code=400, detail="emotion is required (D50)")
    settings = request.app.state.settings
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        emotion_id = resolve_emotion(reader, form.emotion)
    finally:
        reader.close()
    try:
        out = await intake_mod.intake(
            request.app.state.writer,
            principal,
            form,
            emotion_id=emotion_id,
            entropy_threshold=settings.secrets.entropy_threshold,
        )
    except links_mod.LineageNotFound as e2:
        raise HTTPException(status_code=400, detail=f"branch target not found: {e2}") from e2
    except ValueError as e2:
        raise HTTPException(status_code=400, detail=str(e2)) from e2
    metrics.writes_total.labels(route="intake").inc()
    get_logger("api").info(
        "intake",
        extra={"principal_id": principal.principal_id, "route": "POST /v1/intake",
               "type": form.type, "ref": out.ref, "branches": len(out.links)},
    )
    return out


@router.get("/v1/disputes", response_model=DisputeQueueOut)
async def get_disputes(
    request: Request, principal: Principal = Depends(require_principal)
) -> DisputeQueueOut:
    try:
        groups = await request.app.state.writer.tx(
            lambda cur: adjudication.detect_and_mark(cur, principal)
        )
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return DisputeQueueOut(groups=groups)


@router.get("/v1/graph", response_model=None)
async def get_graph(
    request: Request,
    principal: Principal = Depends(require_principal),
    include_superseded: bool = False,
    include_archived: bool = False,
    include_proposed: bool = False,
    limit: int = 0,
    fields: str = "full",
    kinds: str | None = None,
    ids: str | None = None,
    since: int | None = None,
) -> GraphSnapshotOut | GraphSnapshotMinimalOut | GraphDeltaOut:
    settings = request.app.state.settings
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        if since is not None:
            delta = graph_mod.graph_delta(
                reader, principal, since=since, include_proposed=include_proposed
            )
            return GraphDeltaOut.model_validate(delta)
        snap = graph_mod.graph_snapshot(
            reader,
            principal,
            include_superseded=include_superseded,
            include_archived=include_archived,
            include_proposed=include_proposed,
            fields=fields,
            kinds=set(kinds.split(",")) if kinds else None,
            ids=set(ids.split(",")) if ids else None,
            limit=min(limit, 5000),
        )
        if fields == "minimal" and not ids:
            snap["cursor"] = int(
                reader.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
            )
            return GraphSnapshotMinimalOut.model_validate(snap)
    finally:
        reader.close()
    return GraphSnapshotOut.model_validate(snap)


@router.get("/v1/pending")
async def get_pending(
    request: Request, principal: Principal = Depends(require_principal),
    limit: int = 200,
) -> dict[str, object]:
    settings = request.app.state.settings
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        return propose_mod.pending(reader, principal, limit=min(limit, 500))
    finally:
        reader.close()


@router.post("/v1/facts/propose")
async def propose_fact_route(
    body: dict, request: Request,
    principal: Principal = Depends(require_principal),
) -> dict[str, str]:
    claim = str(body.get("claim") or "").strip()
    source = str(body.get("source_lineage") or "").strip()
    if not claim or not source:
        raise HTTPException(status_code=400,
                            detail="claim and source_lineage are required")
    try:
        fact_id = await request.app.state.writer.tx(
            lambda cur: propose_mod.propose_fact(
                cur, principal, body=claim,
                scope=default_write_scope(principal, body.get("scope")),
                source_lineage=source,
                source_note=str(body.get("source_note") or ""),
                confidence=float(body.get("confidence") or 0.5),
            )
        )
    except propose_mod.DuplicateClaim as e:
        return {"fact_id": e.fact_id, "status": "duplicate"}
    return {"fact_id": fact_id, "status": "proposed"}


@router.post("/v1/facts/{fact_id}/approve")
async def approve_fact(
    fact_id: str, request: Request, principal: Principal = Depends(require_principal)
) -> dict[str, str]:
    'The claim becomes a real memory, unchanged, with its provenance intact.'
    try:
        return await request.app.state.writer.tx(
            lambda cur: propose_mod.promote_fact(cur, principal, fact_id))
    except propose_mod.NotProposed as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/v1/facts/{fact_id}/reject")
async def reject_fact_route(
    fact_id: str, request: Request, principal: Principal = Depends(require_principal)
) -> dict[str, str]:
    try:
        return await request.app.state.writer.tx(
            lambda cur: propose_mod.reject_fact(cur, principal, fact_id))
    except propose_mod.NotProposed as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/v1/people/propose")
async def propose_person_route(
    body: dict, request: Request,
    principal: Principal = Depends(require_principal),
) -> dict[str, object]:
    'Queue a name for review. Creates nobody.'
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    pid = await request.app.state.writer.tx(
        lambda cur: propose_mod.propose_person(
            cur, principal, name=name,
            sentence=str(body.get("sentence") or ""),
            relationship=str(body.get("relationship") or ""),
            mentions=int(body.get("mentions") or 0),
        )
    )
    if pid is None:
        return {"status": "known", "name": name}
    return {"status": "proposed", "proposal_id": pid, "name": name}


@router.post("/v1/people/propose-merge")
async def propose_merge_route(
    body: dict, request: Request,
    principal: Principal = Depends(require_principal),
) -> dict[str, object]:
    'Propose that two hubs are one person. Never merges on its own.'
    loser = str(body.get("slug") or "").strip()
    winner = str(body.get("merge_into") or "").strip()
    if not loser or not winner:
        raise HTTPException(status_code=400,
                            detail="slug and merge_into are required")
    if loser == winner:
        raise HTTPException(status_code=400, detail="cannot merge a person into "
                                                    "themselves")
    try:
        pid = await request.app.state.writer.tx(
            lambda cur: propose_mod.propose_merge(
                cur, principal, loser_slug=loser, winner_slug=winner,
                sentence=str(body.get("sentence") or ""),
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if pid is None:
        return {"status": "known", "slug": loser}
    return {"status": "proposed", "proposal_id": pid,
            "slug": loser, "merge_into": winner}


@router.post("/v1/people/{proposal_id}/approve")
async def approve_person_route(
    proposal_id: str, request: Request,
    principal: Principal = Depends(require_principal),
) -> dict[str, str]:
    'Yes: mint the hub and attach the memories that name them.'
    try:
        return await request.app.state.writer.tx(
            lambda cur: propose_mod.approve_person(cur, principal, proposal_id))
    except propose_mod.NotProposed as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.patch("/v1/people/{proposal_id}")
async def edit_person_route(
    proposal_id: str, body: dict, request: Request,
    principal: Principal = Depends(require_principal),
) -> dict:
    'Correct a card before deciding on it. Editing is not approving.'
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="body must be an object")
    name = body.get("name")
    if name is not None and not isinstance(name, str):
        raise HTTPException(status_code=422, detail="name must be a string")
    try:
        return await request.app.state.writer.tx(
            lambda cur: propose_mod.edit_person_proposal(
                cur, principal, proposal_id, name=name,
                sentence=body["sentence"] if "sentence" in body
                else propose_mod.UNSET,
                relationship=body["relationship"] if "relationship" in body
                else propose_mod.UNSET,
            )
        )
    except propose_mod.NotProposed as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except propose_mod.InvalidProposal as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/v1/people/{proposal_id}/alias")
async def alias_person_route(
    proposal_id: str, body: dict, request: Request,
    principal: Principal = Depends(require_principal),
) -> dict[str, str]:
    of = str(body.get("of") or "").strip()
    if not of:
        raise HTTPException(status_code=400, detail="of is required")
    try:
        return await request.app.state.writer.tx(
            lambda cur: propose_mod.alias_person(cur, principal, proposal_id, of=of))
    except propose_mod.NotProposed as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/v1/people/{proposal_id}/reject")
async def reject_person_route(
    proposal_id: str, request: Request,
    principal: Principal = Depends(require_principal),
) -> dict[str, str]:
    'No, permanently. The name is never proposed again.'
    try:
        return await request.app.state.writer.tx(
            lambda cur: propose_mod.reject_person(cur, principal, proposal_id))
    except propose_mod.NotProposed as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/v1/links/pending")
async def get_pending_links(
    request: Request, principal: Principal = Depends(require_principal),
    limit: int = 200,
) -> dict[str, object]:
    'Proposed links awaiting a decision, both ends in plain text.'
    settings = request.app.state.settings
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        scopes = visible_scopes(principal)
        ph = ",".join("?" for _ in scopes)
        rows = reader.execute(
            f"SELECT * FROM memory_links WHERE status='proposed'"
            f" AND scope IN ({ph}) ORDER BY created_at DESC LIMIT ?",
            (*sorted(scopes), min(limit, 500)),
        ).fetchall()

        def head_text(lineage: str) -> str | None:
            for table, pk, col in (("facts", "fact_id", "gloss"),
                                   ("episodes", "episode_id", "summary"),
                                   ("preferences", "preference_id", "statement"),
                                   ("notes", "note_id", "body")):
                r = reader.execute(
                    f"SELECT {col} FROM {table} WHERE lineage_id=? AND is_head=1",
                    (lineage,)).fetchone()
                if r is not None and r[0]:
                    return str(r[0])
            return None

        out: list[dict[str, object]] = []
        for l in rows:
            src, dst = str(l["src_lineage_id"]), str(l["dst_lineage_id"])
            st, dt = head_text(src), head_text(dst)
            out.append({
                "link_id": str(l["link_id"]),
                "link_type": str(l["link_type"]),
                "scope": str(l["scope"]),
                "held_for_review": str(l["scope"]) in scope_mod.OPT_IN_SCOPES,
                "stale": st is None or dt is None,
                "proposed_by": str(l["proposed_by"] or ""),
                "created_at": int(l["created_at"] or 0),
                "src": {"lineage_id": src, "text": (st or "")[:400] or None},
                "dst": {"lineage_id": dst, "text": (dt or "")[:400] or None},
            })
    finally:
        reader.close()
    return {"count": len(out), "links": out}


@router.post("/v1/procedures/{procedure_id}/validate")
async def post_procedure_validate(
    procedure_id: str, request: Request, principal: Principal = Depends(require_principal)
) -> dict[str, str]:
    try:
        row = await request.app.state.writer.tx(
            lambda cur: procedures_mod.validate(cur, principal, procedure_id)
        )
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except procedures_mod.ProcedureNotFound as e:
        raise HTTPException(status_code=404, detail=f"procedure not found: {e}") from e
    metrics.writes_total.labels(route="procedure_validate").inc()
    return {"procedure_id": procedure_id,
            "validation_status": str(row["validation_status"])}


@router.post("/v1/library/search")
async def post_library_search(
    q: LibrarySearchIn, request: Request, principal: Principal = Depends(require_principal)
) -> dict:
    'Search the library. Deliberate, never part of recall.'
    settings = request.app.state.settings
    qvec = await asyncio.to_thread(request.app.state.embeddings.embed_query, q.query)
    if qvec is None:
        get_logger("api").warning(
            "library search degraded to keyword: embeddings unavailable",
            extra={"principal_id": principal.principal_id})
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        return library_mod.search(reader, principal, query=q.query, qvec=qvec,
                                  book=q.book, limit=q.limit)
    except scope_mod.Forbidden as e:
        raise HTTPException(status_code=403, detail=f"no access to {e}") from e
    finally:
        reader.close()


@router.get("/v1/library/books")
async def get_library_books(
    request: Request, principal: Principal = Depends(require_principal)
) -> dict:
    'Every book in the library, with page counts.'
    settings = request.app.state.settings
    if "library" not in set(principal.allowed_scopes):
        raise HTTPException(status_code=403, detail="no access to library")
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        return {"books": library_mod.list_books(reader)}
    finally:
        reader.close()


@router.post("/v1/resolve", response_model=ResolveOut)
async def post_resolve(
    r: ResolveIn, request: Request, principal: Principal = Depends(require_principal)
) -> ResolveOut:
    try:
        winner, loser = await request.app.state.writer.tx(
            lambda cur: adjudication.resolve(cur, principal, r.winner_fact_id, r.loser_fact_id)
        )
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    metrics.writes_total.labels(route="resolve").inc()
    return ResolveOut(
        winner_fact_id=r.winner_fact_id,
        loser_fact_id=r.loser_fact_id,
        winner_status="active",
        loser_status="superseded",
    )


@router.post("/v1/conversations/turns")
async def push_conversation_turns(
    body: dict, request: Request,
    principal: Principal = Depends(require_principal),
) -> dict[str, object]:
    'Agent conversation turns, pushed in live, one or a few at a time.'
    settings = request.app.state.settings
    scope = getattr(settings, "artifact_scope", None) or "reference"
    try:
        return await request.app.state.writer.tx(
            lambda cur: conversations_mod.push(cur, principal, body, scope=scope))
    except conversations_mod.InvalidTurn as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/v1/conversations/{source}/{session_id}")
async def conversation_state(
    source: str, session_id: str, request: Request,
    principal: Principal = Depends(require_principal),
) -> dict[str, object]:
    'Where a live conversation is up to: its hub, and what has yet to seal.'
    settings = request.app.state.settings
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        return conversations_mod.session_state(reader.cursor(), source, session_id)
    finally:
        reader.close()


@router.post("/v1/conversations/seal-idle")
async def seal_idle_conversations(
    request: Request, principal: Principal = Depends(require_principal),
    older_than_ms: int = conversations_mod.IDLE_SEAL_MS,
) -> dict[str, object]:
    'Seal short windows on sessions that have gone quiet.'
    settings = request.app.state.settings
    scope = getattr(settings, "artifact_scope", None) or "reference"
    return await request.app.state.writer.tx(
        lambda cur: conversations_mod.seal_idle(
            cur, principal, scope=scope, older_than_ms=older_than_ms))


def _require_private(principal: Principal) -> None:
    'The single enforcement point for the private store.'
    if not principal.private_authority:
        raise HTTPException(
            status_code=403, detail="this key does not carry private_authority")


def _private_con(request: Request):  # type: ignore[no-untyped-def]
    settings = request.app.state.settings
    return private_mod.open_private(
        settings.db.path, busy_timeout_ms=settings.db.busy_timeout_ms)


@router.post("/v1/private/write")
async def private_write(
    body: dict, request: Request,
    principal: Principal = Depends(require_principal),
) -> dict[str, object]:
    'Write a private memory. Hers; invisible to every other read path.'
    _require_private(principal)
    settings = request.app.state.settings
    text = str(body.get("body") or "")
    emotion = body.get("emotion")
    if not emotion:
        raise HTTPException(status_code=400, detail="emotion is required")

    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        emotion_id = emotions_mod.resolve_emotion(reader, emotion)
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail=f"unknown emotion: {e}") from e
    finally:
        reader.close()

    clean_opt, _hit = secrets_scan.scan_and_redact(text)
    clean = clean_opt or ""
    vector = await asyncio.to_thread(
        request.app.state.embeddings.embed_query, clean)

    con = _private_con(request)
    try:
        if vector is not None:
            private_mod.ensure_vec(con, len(vector))
        out = private_mod.write(
            con, type=str(body.get("type") or "note"), body=clean,
            emotion_id=emotion_id, created_by=principal.principal_id,
            subject_class=str(body.get("subject_class") or "self"),
            tags=list(body.get("tags") or []),
            about_public=list(body.get("about_public") or []),
            vector=vector)
    except private_mod.PrivateError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        con.close()
    return {**out, "degraded": vector is None}


@router.post("/v1/private/search")
async def private_search(
    body: dict, request: Request,
    principal: Principal = Depends(require_principal),
) -> dict[str, object]:
    'Search the private store, and only the private store.'
    _require_private(principal)
    query = str(body.get("query") or "")
    vector = await asyncio.to_thread(
        request.app.state.embeddings.embed_query, query) if query else None
    con = _private_con(request)
    try:
        hits = private_mod.search(
            con, query, limit=int(body.get("limit") or 10), vector=vector)
        return {"query": query, "count": len(hits), "memories": hits}
    finally:
        con.close()


@router.get("/v1/private/recent")
async def private_recent(
    request: Request, principal: Principal = Depends(require_principal),
    limit: int = 20,
) -> dict[str, object]:
    _require_private(principal)
    con = _private_con(request)
    try:
        return {"memories": private_mod.recent(con, limit=limit)}
    finally:
        con.close()


@router.post("/v1/private/forget")
async def private_forget(
    body: dict, request: Request,
    principal: Principal = Depends(require_principal),
) -> dict[str, object]:
    'Delete a private memory outright.'
    _require_private(principal)
    mid = str(body.get("memory_id") or "")
    if not mid:
        raise HTTPException(status_code=400, detail="memory_id is required")
    con = _private_con(request)
    try:
        gone = private_mod.forget(con, mid)
    finally:
        con.close()
    if not gone:
        raise HTTPException(status_code=404, detail=f"no such private memory: {mid}")
    return {"memory_id": mid, "forgotten": True}


@router.get("/v1/conversations")
async def list_pushed_conversations(
    request: Request, principal: Principal = Depends(require_principal),
    source: str | None = None,
) -> dict[str, object]:
    'Every conversation the push path holds.'
    settings = request.app.state.settings
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        return {"conversations": conversations_mod.sessions(reader.cursor(), source)}
    finally:
        reader.close()


@router.get("/v1/conversations/{source}/{session_id}/transcript")
async def read_conversation(
    source: str, session_id: str, request: Request,
    principal: Principal = Depends(require_principal),
) -> dict[str, object]:
    'One conversation, read by walking `part_of` from its recorded hub.'
    settings = request.app.state.settings
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        out = conversations_mod.transcript(reader.cursor(), source, session_id)
    finally:
        reader.close()
    if out is None:
        raise HTTPException(
            status_code=404, detail=f"no conversation {source}/{session_id}")
    return out
