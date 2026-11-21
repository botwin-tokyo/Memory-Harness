from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from memory_contract import TOOLS as _CONTRACT_TOOLS
from memory_mcp import server as srv
from memory_mcp.runtime import current_runtime

log = logging.getLogger("memory_mcp.tools")

mcp = srv.mcp


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    'Drop None values so the harness sees only what the agent actually set.'
    return {k: v for k, v in payload.items() if v is not None}


def _log_write(tool: str, result: dict[str, Any]) -> None:
    log.info(
        "mcp_write tool=%s runtime=%s ref=%s",
        tool,
        current_runtime(),
        result.get("ref") or result.get("link_id") or result.get("retracted"),
    )


@mcp.tool(description=_CONTRACT_TOOLS["recall"].description)
async def recall(
    query: str,
    token_budget: int = 8000,
    scope_filter: list[str] | None = None,
    type_filter: list[str] | None = None,
    include_links: bool = False,
) -> dict[str, Any]:
    'Retrieve your memory about a topic as a budgeted context pack.'
    return await srv.harness.recall(_clean({
        "query": query,
        "token_budget": token_budget,
        "scope_filter": scope_filter,
        "type_filter": type_filter,
        "include_links": include_links,
    }))


@mcp.tool()
async def remember(
    type: str,
    body: str,
    emotion: str,
    subject: str | None = None,
    subject_class: str | None = None,
    about: list[str] | None = None,
    tags: list[str] | None = None,
    memory_id: str | None = None,
    confidence: float | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    'Write one durable memory. Returns the minted `lineage_id.revision`.'
    result = await srv.harness.remember(_clean({
        "type": type,
        "body": body,
        "emotion": emotion,
        "subject": subject,
        "subject_class": subject_class,
        "about": about,
        "tags": tags,
        "memory_id": memory_id,
        "category": category,
        "confidence": confidence,
    }))
    _log_write("remember", result)
    return result


@mcp.tool()
async def intake(
    type: str,
    body: str,
    emotion: str,
    subject: str | None = None,
    about: list[str] | None = None,
    tags: list[str] | None = None,
    memory_id: str | None = None,
    category: str | None = None,
    branches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    'Write a memory AND wire its graph links in one atomic call.'
    result = await srv.harness.intake(_clean({
        "type": type,
        "body": body,
        "emotion": emotion,
        "subject": subject,
        "about": about,
        "tags": tags,
        "memory_id": memory_id,
        "category": category,
        "branches": branches,
    }))
    _log_write("intake", result)
    return result


@mcp.tool(description=_CONTRACT_TOOLS["explore"].description)
async def explore(
    start_id: str,
    depth: int = 2,
    link_types: list[str] | None = None,
    direction: str = "out",
) -> dict[str, Any]:
    'Walk the memory graph from one memory (BFS over active links).'
    return await srv.harness.explore(_clean({
        "start_id": start_id,
        "depth": depth,
        "link_types": link_types,
        "direction": direction if direction != "out" else None,
    }))


@mcp.tool()
async def history(lineage_id: str) -> dict[str, Any]:
    'Read every revision of one memory, oldest → newest, HEAD flagged.'
    return await srv.harness.history(lineage_id)


@mcp.tool()
async def link(
    src_lineage_id: str,
    dst_lineage_id: str,
    link_type: str,
    weight: float | None = None,
) -> dict[str, Any]:
    'Propose a typed edge between two existing memories.'
    result = await srv.harness.link(_clean({
        "src_lineage_id": src_lineage_id,
        "dst_lineage_id": dst_lineage_id,
        "link_type": link_type,
        "weight": weight,
    }))
    _log_write("link", result)
    return result


@mcp.tool()
async def forget(
    id: str | None = None,
    type: str | None = None,
    lineage_id: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    'Forget.'
    if not reason:
        raise ValueError("`reason` is required — say why this should be forgotten")
    payload: dict[str, Any] = {"reason": reason}
    if id is not None:
        payload["id"] = id
    elif type is not None and lineage_id is not None:
        payload["selector"] = {"type": type, "lineage_id": lineage_id}
    else:
        raise ValueError("give `id`, or `type` + `lineage_id`")
    result = await srv.harness.forget(payload)
    _log_write("forget", result)
    return result


@mcp.tool()
async def emotions() -> list[dict[str, Any]]:
    'The 127-emotion vocabulary (id, name, family, description).'
    return await srv.harness.emotions()


@mcp.tool()
async def ingest(
    source: str,
    link_to: str | None = None,
    kind: str = "document",
    title: str | None = None,
    about: str | None = None,
    force_render: bool = False,
) -> dict[str, Any]:
    'Ingest.'
    import httpx as _httpx

    try:
        async with _httpx.AsyncClient(timeout=_httpx.Timeout(600.0)) as client:
            r = await client.post(f"{srv.settings.ingest_base}/ingest", json=_clean({
                "source": source, "kind": kind, "title": title,
                "about": about, "link_to": link_to,
                "force_render": force_render or None,
            }))
    except _httpx.HTTPError as exc:
        raise ToolError(f"ingest service unreachable: {exc}") from exc
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise ToolError(f"ingest failed ({r.status_code}): {detail}")
    result: dict[str, Any] = r.json()
    log.info(
        "mcp_write tool=ingest runtime=%s source=%s parent=%s chunks=%s",
        current_runtime(), source, result.get("parent_lineage"), result.get("chunks"),
    )
    return result


@mcp.tool()
async def attach(
    src_memory_id: str,
    dst_id: str,
    link_type: str = "detailed_by",
) -> dict[str, Any]:
    'Attach.'
    result = await srv.harness.link(_clean({
        "src_lineage_id": src_memory_id,
        "dst_lineage_id": dst_id,
        "link_type": link_type,
    }))
    _log_write("attach", result)
    return result


async def _git_call(path: str, payload: dict[str, Any], timeout_s: float = 1800.0) -> dict[str, Any]:
    import httpx as _httpx

    try:
        async with _httpx.AsyncClient(timeout=_httpx.Timeout(timeout_s)) as client:
            r = await client.post(f"{srv.settings.ingest_base}{path}",
                                  json=_clean(payload))
    except _httpx.HTTPError as exc:
        raise ToolError(f"ingest service unreachable: {exc}") from exc
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise ToolError(f"git tier failed ({r.status_code}): {detail}")
    return r.json()  # type: ignore[no-any-return]


@mcp.tool()
async def git_index(repo: str, link_to: str | None = None) -> dict[str, Any]:
    'Git index.'
    result = await _git_call("/git/index", {"repo": repo, "link_to": link_to})
    log.info("mcp_write tool=git_index runtime=%s repo=%s rev=%s",
             current_runtime(), repo, result.get("revision"))
    return result


@mcp.tool()
async def fetch_file(repo: str, path: str, sha: str | None = None) -> dict[str, Any]:
    'Read ONE file from a git repo, verbatim, at the pinned sha.'
    result = await _git_call("/git/fetch_file",
                             {"repo": repo, "path": path, "sha": sha},
                             timeout_s=600.0)
    log.info("mcp_read tool=fetch_file runtime=%s repo=%s path=%s",
             current_runtime(), repo, path)
    return result


@mcp.tool()
async def ingest_repo(repo: str, link_to: str | None = None) -> dict[str, Any]:
    'Ingest repo.'
    result = await _git_call("/git/ingest_repo", {"repo": repo, "link_to": link_to})
    log.info("mcp_write tool=ingest_repo runtime=%s repo=%s +%s ~%s -%s",
             current_runtime(), repo, result.get("files_written"),
             result.get("files_revised"), result.get("files_forgotten"))
    return result


@mcp.tool()
async def quote_source(
    target: str,
    query: str | None = None,
    offset: int = 0,
    limit: int | None = None,
    window: int = 1,
    max_tokens: int = 1500,
) -> str:
    'Quote source.'
    payload = _clean({
        "target": target, "query": query, "offset": offset or None,
        "limit": limit, "window": window, "token_budget": max_tokens,
    })
    payload.setdefault("offset", 0)
    result = await srv.harness.source(payload)
    log.info("mcp_read tool=quote_source runtime=%s target=%s mode=%s returned=%s",
             current_runtime(), target, result.get("mode"), result.get("returned"))
    if not (result.get("chunks") or []):
        result = dict(result)
        result["note"] = (
            (result.get("note") or "this memory grounds no source")
            + ". If this is a git memory (`[file]`, `[commit]`, `[repo]`), its "
              "text is not an artifact — read it with `fetch_file(repo, path)` "
              "instead."
        )
    return _format_source(result)


def _format_source(r: dict[str, Any]) -> str:
    'A clean quotable block: citation header, then the passages in order.'
    s = r.get("source") or {}
    title = s.get("title") or "(untitled source)"
    ref = s.get("ref") or ""
    sha = s.get("sha256") or ""
    head = f"{title}"
    if ref:
        head += f" — {ref}"
    if sha:
        head += f" ({sha[:16]})"

    lines = [head, "=" * min(len(head), 76)]
    chunks = r.get("chunks") or []
    if not chunks:
        lines.append(r.get("note") or "No source text available.")
        return "\n".join(lines)

    for c in chunks:
        marker = "▸" if c.get("relevance") is not None else " "
        lines.append(f"\n{marker} [passage {c['index']}]")
        lines.append(c["text"])

    footer = [
        "",
        f"— {r.get('returned', len(chunks))} of {r.get('total_chunks', '?')} passages"
        + (" (truncated; raise max_tokens or page with offset)" if r.get("truncated") else ""),
    ]
    if r.get("mode") == "search":
        footer.append("  ▸ marks the passages that matched; the rest is context.")
    if r.get("grounded_memory"):
        footer.append(f"  source of memory {r['grounded_memory']}")
    if r.get("note"):
        footer.append(f"  note: {r['note']}")
    return "\n".join(lines + footer)


async def _project_call(path: str, payload: dict[str, Any] | None = None,
                        timeout_s: float = 3600.0) -> dict[str, Any]:
    import httpx as _httpx

    url = f"{srv.settings.ingest_base}{path}"
    try:
        async with _httpx.AsyncClient(timeout=_httpx.Timeout(timeout_s)) as client:
            r = (await client.post(url, json=_clean(payload or {}))
                 if payload is not None else await client.get(url))
    except _httpx.HTTPError as exc:
        raise ToolError(f"ingest service unreachable: {exc}") from exc
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise ToolError(f"project call failed ({r.status_code}): {detail}")
    return r.json()  # type: ignore[no-any-return]


@mcp.tool()
async def ingest_project(path: str, name: str | None = None,
                         ignore: list[str] | None = None) -> dict[str, Any]:
    'Read a project FOLDER into memory as a browsable subtree.'
    result = await _project_call("/project/ingest",
                                 {"root": path, "project": name, "ignore": ignore})
    log.info("mcp_write tool=ingest_project runtime=%s project=%s docs=%s",
             current_runtime(), result.get("project"), result.get("documents"))
    return result


@mcp.tool()
async def list_projects() -> dict[str, Any]:
    'Every project folder that has been read into memory, from any node.'
    return await _project_call("/project/list", None, timeout_s=60.0)


@mcp.tool()
async def project_tree(project: str) -> str:
    'Project tree.'
    pack = await srv.harness.recall({
        "query": f"PROJECT {project} tree", "token_budget": 3000,
        "scope_filter": ["reference"],
    })
    for item in pack.get("facts", []) + pack.get("core", []):
        text = item.get("text", "")
        if text.startswith(f"# PROJECT — {project}"):
            return text
    raise ToolError(
        f"no project map found for {project!r} — `list_projects` shows what is "
        "in the graph, or `ingest_project` to read it in"
    )


@mcp.tool()
async def find_in_project(project: str, query: str,
                          max_tokens: int = 1500) -> dict[str, Any]:
    "Search inside ONE project's documents and quote what you find."
    owned = await _project_nodes(project)
    if not owned["known"]:
        raise ToolError(
            f"no project {project!r} — `list_projects` shows what is in the "
            "graph, or `ingest_project` to read it in")
    lineages = set(owned["lineages"])

    pack = await srv.harness.recall({
        "query": query, "token_budget": max_tokens, "scope_filter": ["reference"],
    })
    hits = []
    for item in pack.get("facts", []) + pack.get("episodes", []):
        if str(item.get("lineage_id")) in lineages:
            hits.append({"text": item.get("text", ""),
                         "lineage_id": item.get("lineage_id"),
                         "scope": item.get("scope")})
    return {"project": project, "query": query, "hits": hits,
            "note": None if hits else
            f"nothing in {project} matched {query!r} — `project_tree` shows "
            "what is there"}


async def _project_nodes(project: str) -> dict[str, Any]:
    'The lineages a project owns, from the ledger that tracks them.'
    import httpx as _httpx

    url = f"{srv.settings.ingest_base}/project/nodes"
    try:
        async with _httpx.AsyncClient(timeout=_httpx.Timeout(60.0)) as client:
            r = await client.get(url, params={"project": project})
    except _httpx.HTTPError as exc:
        raise ToolError(f"ingest service unreachable: {exc}") from exc
    if r.status_code >= 400:
        raise ToolError(f"project call failed ({r.status_code}): {r.text[:200]}")
    return r.json()  # type: ignore[no-any-return]


@mcp.tool()
async def get_document(project: str, relpath: str) -> dict[str, Any]:
    'Get document.'
    result = await _project_call("/project/fetch_file",
                                 {"project": project, "relpath": relpath},
                                 timeout_s=300.0)
    log.info("mcp_read tool=get_document runtime=%s project=%s path=%s",
             current_runtime(), project, relpath)
    return result


@mcp.tool()
async def link_note_to_project(note_lineage: str, project: str,
                               relpath: str | None = None) -> dict[str, Any]:
    'Link note to project.'
    result = await _project_call("/project/link_note", {
        "note_lineage": note_lineage, "project": project, "relpath": relpath,
    }, timeout_s=300.0)
    log.info("mcp_write tool=link_note_to_project runtime=%s project=%s target=%s",
             current_runtime(), project, result.get("target"))
    return result


@mcp.tool()
async def ingest_conversations(source: str, path: str,
                               agent: str | None = None) -> dict[str, Any]:
    "Pull this node's agent conversations into memory (`hermes` | `openclaw`)."
    result = await _project_call("/conversations/ingest",
                                 {"source": source, "path": path, "agent": agent},
                                 timeout_s=3600.0)
    log.info("mcp_write tool=ingest_conversations runtime=%s source=%s turns=%s",
             current_runtime(), source, result.get("turns"))
    return result


@mcp.tool()
async def list_conversations(source: str | None = None) -> dict[str, Any]:
    'Every conversation that has been mirrored into memory, from any node.'
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    try:
        for c in (await srv.harness.conversations(source)).get("conversations") or []:
            out.append(c)
            seen.add((str(c.get("source")), str(c.get("session_id"))))
    except Exception as exc:  # noqa: BLE001 — one store being down must not
        log.warning("push-side conversation listing unavailable: %s", exc)

    suffix = f"?source={source}" if source else ""
    try:
        listed = await _project_call(f"/conversations/list{suffix}", None,
                                     timeout_s=60.0)
    except Exception as exc:  # noqa: BLE001 — ...hide the other entirely
        log.warning("ledger conversation listing unavailable: %s", exc)
        listed = {"conversations": []}

    gone: list[str] = []
    for c in list(listed.get("conversations") or []):
        key = (str(c.get("source")), str(c.get("session_id")))
        if key in seen:
            continue
        hub = c.get("hub_lineage")
        if hub and not await _lineage_is_live(str(hub)):
            gone.append(str(c.get("session_id")))
        out.append({**c, "via": "sweep"})

    result: dict[str, Any] = {"conversations": out}
    if gone:
        result["unavailable"] = gone
        result["note"] = (
            f"{len(gone)} conversation(s) were mirrored but their hub is no "
            "longer in the graph — `get_conversation` cannot open them.")
    return result


async def _lineage_is_live(lineage: str) -> bool:
    'Is this hub still a visible node? `/v1/graph` never emits retracted rows.'
    try:
        got = await srv.harness.graph_ids(lineage)
    except Exception:  # noqa: BLE001 — a listing must not fail on one probe
        return True
    return bool(got.get("nodes"))


@mcp.tool()
async def find_in_conversation(query: str, max_tokens: int = 1500) -> dict[str, Any]:
    'Search what was actually SAID in past conversations, and quote it.'
    pack = await srv.harness.recall({
        "query": query, "token_budget": max_tokens, "scope_filter": ["reference"],
    })
    hits = []
    for item in pack.get("facts", []) + pack.get("episodes", []):
        text = item.get("text", "")
        if text.lstrip().startswith("["):
            hits.append({"lineage_id": item.get("lineage_id"), "text": text})
    return {"query": query, "turns": hits}


@mcp.tool()
async def get_conversation(session: str) -> dict[str, Any]:
    'Read back one whole conversation by its session id.'
    for src in ("hermes", "openclaw"):
        try:
            got = await srv.harness.transcript(src, session)
        except ToolError:
            continue
        return {
            "session": session, "source": got.get("source"),
            "agent": got.get("agent"), "model": got.get("model"),
            "header": got.get("header"),
            "turns_total": got.get("turns_total"),
            "windows": got.get("windows") or [],
        }
    raise ToolError(
        f"no conversation {session!r} in memory — try list_conversations")


@mcp.tool()
async def link_conversation_to_project(session: str, project: str) -> dict[str, Any]:
    'Tie a conversation to the project it was about.'
    convo = await get_conversation(session)
    lineages = [t for t in convo.get("turns", [])]
    if not lineages:
        raise ToolError(f"conversation {session!r} has no turns to link")
    result = await _project_call("/project/link_note",
                                 {"note": convo["header"], "project": project})
    log.info("mcp_write tool=link_conversation_to_project runtime=%s session=%s",
             current_runtime(), session)
    return result


@mcp.tool(description=_CONTRACT_TOOLS["list_books"].description)
async def list_books() -> dict[str, Any]:
    'Every book in your library, with author and page count.'
    return await srv.harness.library_books()


@mcp.tool(description=_CONTRACT_TOOLS["search_library"].description)
async def search_library(query: str, book: str | None = None,
                         limit: int = 6) -> dict[str, Any]:
    'Look something up in your books. Choose this deliberately.'
    return await srv.harness.library_search(
        {"query": query, "book": book, "limit": limit})


@mcp.tool(description=_CONTRACT_TOOLS["read_book_passage"].description)
async def read_book_passage(book: str, query: str,
                            limit: int = 3) -> dict[str, Any]:
    'Read specific passages from ONE book, verbatim, with page numbers.'
    return await srv.harness.library_search(
        {"query": query, "book": book, "limit": limit})


@mcp.tool()
async def get_book(book: str) -> dict[str, Any]:
    "One book's title, author, publication year, synopsis and length."
    out = await srv.harness.library_books()
    for b in out.get("books", []):
        if b.get("book") == book:
            return b
    raise ToolError(f"no book {book!r} in the library — try list_books")


@mcp.tool()
async def link_book_to_memory(book: str, memory_lineage: str,
                              note: str | None = None) -> dict[str, Any]:
    'Connect a book to one of your memories. The ONLY way the two ever meet.'
    books = await srv.harness.library_books()
    hub = next((b["lineage_id"] for b in books.get("books", [])
                if b.get("book") == book), None)
    if hub is None:
        raise ToolError(f"no book {book!r} in the library — try list_books")
    result = await srv.harness.link({
        "src_lineage_id": memory_lineage, "dst_lineage_id": hub,
        "link_type": "relates_to"})
    log.info("mcp_write tool=link_book_to_memory runtime=%s book=%s",
             current_runtime(), book)
    return {"book": book, "book_lineage": hub, "memory": memory_lineage,
            "link": result, "note": note}


@mcp.tool()
async def write_private_memory(
    body: str,
    emotion: str,
    type: str = "note",
    tags: list[str] | None = None,
    about_public: list[str] | None = None,
) -> dict[str, Any]:
    'Keep something to yourself.'
    result = await srv.harness.private_write(_clean({
        "type": type, "body": body, "emotion": emotion,
        "tags": tags, "about_public": about_public,
    }))
    log.info("mcp_write tool=write_private_memory runtime=%s", current_runtime())
    return result


@mcp.tool()
async def search_private_memory(query: str, limit: int = 10) -> dict[str, Any]:
    'Search your private memories, and only those.'
    return await srv.harness.private_search({"query": query, "limit": limit})


@mcp.tool()
async def list_private_memories(limit: int = 20) -> dict[str, Any]:
    'List private memories.'
    return await srv.harness.private_recent(limit)


@mcp.tool()
async def forget_private_memory(memory_id: str) -> dict[str, Any]:
    'Delete a private memory. Really delete it.'
    result = await srv.harness.private_forget({"memory_id": memory_id})
    log.info("mcp_write tool=forget_private_memory runtime=%s", current_runtime())
    return result


async def _blog_call(path: str) -> dict[str, Any]:
    'GET a seed-key-gated ingest route.'
    import httpx as _httpx

    if not srv.settings.seed_api_key:
        raise ToolError(
            "Configure SEED_API_KEY for the ingest service and MCP adapter."
        )
    url = f"{srv.settings.ingest_base}{path}"
    headers = {"Authorization": f"Bearer {srv.settings.seed_api_key}"}
    try:
        async with _httpx.AsyncClient(timeout=_httpx.Timeout(60.0)) as client:
            r = await client.get(url, headers=headers)
    except _httpx.HTTPError as exc:
        raise ToolError(f"ingest service unreachable: {exc}") from exc
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise ToolError(f"blog call failed ({r.status_code}): {detail}")
    return r.json()  # type: ignore[no-any-return]


@mcp.tool()
async def list_blog_posts() -> dict[str, Any]:
    'List blog posts.'
    out = await _blog_call("/blog/list")
    posts = [
        {
            "guid": p.get("guid"),
            "title": p.get("title"),
            "date": p.get("date"),
            "url": p.get("url"),
            "hub": p.get("hub_lineage"),
            "article": p.get("artifact_lineage"),
            "chunks": p.get("chunks"),
            "paired_with_journal": bool(p.get("same_day")),
        }
        for p in (out.get("posts") or [])
    ]
    return {"posts": posts, "count": len(posts)}


@mcp.tool()
async def get_blog_post(guid: str) -> dict[str, Any]:
    'One blog post by its feed guid (`post_…`), with both its lineage ids.'
    out = await _blog_call("/blog/list")
    for p in out.get("posts") or []:
        if str(p.get("guid")) == guid:
            return {
                "guid": p.get("guid"),
                "title": p.get("title"),
                "date": p.get("date"),
                "url": p.get("url"),
                "hub": p.get("hub_lineage"),
                "article": p.get("artifact_lineage"),
                "chunks": p.get("chunks"),
                "paired_with_journal": bool(p.get("same_day")),
                "updated_at": p.get("updated_at"),
            }
    raise ToolError(
        f"no blog post with guid {guid!r} has been mirrored. "
        "`list_blog_posts` shows what has."
    )


async def _blog_post_call(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    'POST a seed-key-gated ingest route.'
    import httpx as _httpx

    if not srv.settings.seed_api_key:
        raise ToolError(
            "Configure SEED_API_KEY for the ingest service and MCP adapter."
        )
    url = f"{srv.settings.ingest_base}{path}"
    headers = {"Authorization": f"Bearer {srv.settings.seed_api_key}"}
    try:
        async with _httpx.AsyncClient(timeout=_httpx.Timeout(1800.0)) as client:
            r = await client.post(url, json=payload, headers=headers)
    except _httpx.HTTPError as exc:
        raise ToolError(f"ingest service unreachable: {exc}") from exc
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise ToolError(f"blog call failed ({r.status_code}): {detail}")
    return r.json()  # type: ignore[no-any-return]


@mcp.tool()
async def backfill_blog(dry_run: bool = True, limit: int = 20) -> dict[str, Any]:
    'Find posts on the blog that are not yet in memory, and store them.'
    out = await _blog_post_call("/blog/backfill",
                                {"dry_run": dry_run, "limit": limit})
    if not dry_run:
        log.info("mcp_write tool=backfill_blog runtime=%s written=%s failed=%s",
                 current_runtime(), len(out.get("written") or []),
                 len(out.get("failed") or []))
    return out
