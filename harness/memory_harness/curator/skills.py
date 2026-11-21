from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from memory_harness.curator.links import curate_links
from memory_harness.curator.main import Curator

router = APIRouter()


def _c(request: Request) -> Curator:
    return request.app.state.curator


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, object]:
    c = _c(request)
    harness_ok = False
    pending = None
    try:
        r = c.harness.get("/healthz")
        harness_ok = r.status_code == 200
        feed = c.harness.get(
            "/v1/changes", params={"since": 0, "types": "link_proposed"}, headers=c.headers
        )
        if feed.status_code == 200:
            pending = sum(
                1 for i in feed.json()["items"] if i["record"] and i["record"]["status"] == "proposed"
            )
    except Exception:
        harness_ok = False
    return {
        "status": "ok",
        "harness_reachable": harness_ok,
        "proposed_links_pending": pending,
        "cursor": c.cursor,
    }


@router.get("/links/pending")
async def pending_links(request: Request) -> dict[str, object]:
    'Every proposed link, with BOTH ends in plain text, ready to judge.'
    from memory_harness.curator.links import _gloss
    from memory_harness.domain.scope import OPT_IN_SCOPES

    c = _c(request)
    feed = c.harness.get(
        "/v1/changes", params={"since": 0, "types": "link_proposed", "limit": 500},
        headers=c.headers,
    )
    if feed.status_code != 200:
        raise HTTPException(status_code=feed.status_code, detail=feed.text)

    out: list[dict[str, object]] = []
    for item in feed.json()["items"]:
        rec = item["record"]
        if not rec or rec["status"] != "proposed":
            continue
        src, dst = str(rec["src_lineage_id"]), str(rec["dst_lineage_id"])
        src_text, dst_text = _gloss(c, src) or None, _gloss(c, dst) or None
        out.append({
            "link_id": rec["link_id"],
            "link_type": rec["link_type"],
            "scope": rec.get("scope"),
            "held_for_review": str(rec.get("scope") or "") in OPT_IN_SCOPES,
            "stale": src_text is None or dst_text is None,
            "proposed_by": rec.get("proposed_by"),
            "src": {"lineage_id": src, "text": (src_text or "")[:200] or None},
            "dst": {"lineage_id": dst, "text": (dst_text or "")[:200] or None},
        })
    return {"count": len(out), "links": out,
            "approve": "POST /links/{link_id}/approve",
            "reject": "POST /links/{link_id}/reject"}


@router.post("/curate/run")
async def curate_run(request: Request) -> dict[str, list[str]]:
    return curate_links(_c(request))


@router.post("/links/{link_id}/approve")
async def approve(link_id: str, request: Request) -> dict[str, object]:
    c = _c(request)
    r = c.harness.post(
        "/v1/link", json={"link_id": link_id, "action": "promote"}, headers=c.headers
    )
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


@router.post("/links/{link_id}/reject")
async def reject(link_id: str, request: Request) -> dict[str, object]:
    c = _c(request)
    r = c.harness.post(
        "/v1/link", json={"link_id": link_id, "action": "reject"}, headers=c.headers
    )
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


@router.get("/canary")
async def canary(request: Request) -> dict[str, object]:
    from memory_harness.config import get_settings
    from memory_harness.curator import canary as canary_mod
    from memory_harness.db.connection import open_reader

    con = open_reader(get_settings().db.path)
    try:
        return canary_mod.run(_c(request), con).as_dict()
    finally:
        con.close()


@router.get("/nightly")
async def nightly(request: Request) -> dict[str, object]:
    'When did the nightly window last run, and where is it up to?'
    import time as _time

    from memory_harness.curator.booklinks import is_off_hours
    from memory_harness.curator.main import CURSOR_FILE, _load_state

    c = _c(request)
    state = dict(c.extra) or _load_state()
    last = state.get("last_nightly_run")
    return {
        "off_hours_now": is_off_hours(),
        "last_run_epoch": last,
        "last_run_ago_hours": (
            round((_time.time() - float(str(last))) / 3600, 2) if last else None),
        "journal_cursor": state.get("facts_cursor"),
        "people_cursor": state.get("people_cursor"),
        "cursor_file": str(CURSOR_FILE),
        "note": (
            "A window that runs and proposes nothing is the normal case once a "
            "queue is cleared: the extractor re-derives the same claims from "
            "the same days and the dedup refuses them."
        ),
    }
