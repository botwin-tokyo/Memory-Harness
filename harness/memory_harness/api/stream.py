from __future__ import annotations

import asyncio
import contextlib
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from memory_harness.auth.principal import resolve
from memory_harness.db.connection import open_reader
from memory_harness.domain import changes as changes_mod
from memory_harness.models.domain import Principal
from memory_harness.obs.logging import get_logger

router = APIRouter()

_TICK_S = 1.0

_PING_EVERY_S = 20.0

_CHUNK = 200


def _bearer(ws: WebSocket) -> str | None:
    'Token from the Authorization header, or `?token=` as a fallback.'
    header = ws.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return ws.query_params.get("token") or None


@router.websocket("/v1/stream")
async def stream(ws: WebSocket) -> None:
    settings = ws.app.state.settings
    token = _bearer(ws)
    principal: Principal | None = None
    if token:
        reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
        try:
            principal = resolve(reader, token)
        finally:
            reader.close()
    if principal is None:
        await ws.close(code=1008, reason="invalid or missing bearer token")
        return

    try:
        cursor = int(ws.query_params.get("since") or 0)
    except ValueError:
        cursor = 0

    await ws.accept()
    log = get_logger("api")
    log.info("stream opened", extra={"principal_id": principal.principal_id,
                                     "route": "WS /v1/stream", "since": cursor})
    last_ping = asyncio.get_running_loop().time()

    try:
        while True:
            reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
            try:
                out = await asyncio.to_thread(
                    changes_mod.changes, reader, principal,
                    since=cursor, limit=_CHUNK,
                )
            finally:
                reader.close()

            items = out.items
            for item in items:
                await ws.send_text(json.dumps(item.model_dump(mode="json")))
            cursor = int(out.next_cursor)

            if len(items) == _CHUNK:
                continue

            now = asyncio.get_running_loop().time()
            if now - last_ping >= _PING_EVERY_S:
                last_ping = now
                await ws.send_text(json.dumps({"type": "ping", "cursor": cursor}))

            await asyncio.sleep(_TICK_S)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - a broken socket must not disturb the app
        log.info("stream closed on error",
                 extra={"principal_id": principal.principal_id,
                        "route": "WS /v1/stream"})
        with contextlib.suppress(Exception):
            await ws.close()
