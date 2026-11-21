'The WebSocket telemetry publisher.'

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import websockets

from memory_fleet.collector import Collector, safe_metrics, safe_snapshot
from memory_fleet.config import FleetConfig
from memory_fleet.design import (
    BATCH_EVENTS_MAX,
    EVENT_BYTES_MAX,
    FRAME_BYTES_MAX,
    KIND_HEALTH,
    KIND_HEARTBEAT,
    KIND_LIFECYCLE,
    KIND_METRIC,
    PAYLOAD_SCHEMA_VERSION,
    PROTOCOL_VERSION,
    schema_id,
)

log = logging.getLogger("memory_fleet.publisher")

PUBLISHER_VERSION = "memory-fleet/0.1.0"

HELLO_DEADLINE_S = 5.0
BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 60.0
TICK_S = 0.5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _short_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class _Queued:
    'A spooled event plus the connection generation it was queued under.'

    event: dict[str, Any]
    gen: int
    encoded_len: int


class FleetPublisher:
    def __init__(self, config: FleetConfig, collector: Collector) -> None:
        self.cfg = config
        self.collector = collector
        self.boot_id = _short_id()
        self._seq = 0
        self._gen = 0
        self._spool: deque[_Queued] = deque()
        self._inflight: dict[str, list[_Queued]] = {}
        self._challenge: str | None = None
        self._manifest_revision = 1
        self._heartbeat_s = float(config.heartbeat_s)
        self._started_at = time.monotonic()
        self._last_error = ""
        self._last_health: str | None = None
        self._stopping = False
        self.dropped_overflow = 0
        self.dropped_oversize = 0
        self.rejected_total = 0


    def _envelope(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._seq += 1
        return {
            "protocol_version": PROTOCOL_VERSION,
            "event_id": f"{self.cfg.source_id}-{self.boot_id}-{self._seq}"[:64],
            "node_id": self.cfg.node_id,
            "source_id": self.cfg.source_id,
            "boot_id": self.boot_id,
            "seq": self._seq,
            "kind": kind,
            "schema_id": schema_id(self.cfg.node_id, self.cfg.source_id, kind),
            "schema_version": PAYLOAD_SCHEMA_VERSION,
            "observed_at": _now_iso(),
            "payload": payload,
        }

    def _enqueue(self, event: dict[str, Any]) -> None:
        'Spool an event, dropping the OLDEST on overflow.'
        encoded = json.dumps(event, separators=(",", ":"))
        if len(encoded.encode()) > EVENT_BYTES_MAX:
            self.dropped_oversize += 1
            log.warning(
                "dropping oversize event", extra={"kind": event.get("kind"),
                                                  "bytes": len(encoded.encode())}
            )
            return
        while len(self._spool) >= self.cfg.spool_max:
            self._spool.popleft()
            self.dropped_overflow += 1
        self._spool.append(
            _Queued(event=event, gen=self._gen, encoded_len=len(encoded.encode()))
        )

    def emit_lifecycle(self, state: str, reason: str = "") -> None:
        self._enqueue(self._envelope(KIND_LIFECYCLE, {"state": state, "reason": reason}))

    def _sample(self) -> None:
        'One health + metrics pass onto the spool.'
        snap = safe_snapshot(self.collector)
        self._enqueue(
            self._envelope(
                KIND_HEALTH,
                {
                    "status": snap.status,
                    "summary": snap.summary,
                    "checks": [
                        {"name": c.name, "status": c.status} for c in snap.checks
                    ],
                },
            )
        )
        if snap.status != self._last_health:
            log.info(
                "fleet health transition",
                extra={
                    "from": self._last_health,
                    "to": snap.status,
                    "detail": "; ".join(
                        f"{c.name}={c.status}:{c.detail}"
                        for c in snap.checks
                        if c.status != "healthy"
                    ),
                },
            )
            self._last_health = snap.status

        for sample in safe_metrics(self.collector):
            self._enqueue(
                self._envelope(
                    KIND_METRIC,
                    {
                        "name": sample.name,
                        "value": float(sample.value),
                        "unit": sample.unit,
                        "labels": dict(sample.labels),
                    },
                )
            )

    def _heartbeat_event(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "uptime_s": round(time.monotonic() - self._started_at, 3),
            "publisher_version": PUBLISHER_VERSION,
            "spool_depth": len(self._spool) + sum(len(v) for v in self._inflight.values()),
        }
        if self._last_error:
            payload["last_error"] = self._last_error[:4096]
        return self._envelope(KIND_HEARTBEAT, payload)


    def _take_batch(self) -> tuple[list[_Queued], bool] | None:
        "Pop a frame's worth of spooled events that share a replay-ness."
        if not self._spool:
            return None
        replay = self._spool[0].gen < self._gen
        batch: list[_Queued] = []
        size = 0
        while self._spool and len(batch) < BATCH_EVENTS_MAX:
            head = self._spool[0]
            if (head.gen < self._gen) != replay:
                break
            if size + head.encoded_len + 2 > FRAME_BYTES_MAX - 512:
                break
            batch.append(self._spool.popleft())
            size += head.encoded_len + 2
        return (batch, replay) if batch else None

    async def _send_batch(self, ws: Any, batch: list[_Queued], replay: bool) -> None:
        batch_id = _short_id()
        frame = {
            "type": "events",
            "batch_id": batch_id,
            "replay": replay,
            "challenge": self._challenge,
            "events": [q.event for q in batch],
        }
        self._inflight[batch_id] = batch
        await ws.send(json.dumps(frame, separators=(",", ":")))

    async def _send_heartbeat(self, ws: Any) -> None:
        'A single fresh event, challenge-bound, never spooled.'
        frame = {
            "type": "events",
            "batch_id": _short_id(),
            "replay": False,
            "challenge": self._challenge,
            "events": [self._heartbeat_event()],
        }
        await ws.send(json.dumps(frame, separators=(",", ":")))

    def _handle_ack(self, frame: dict[str, Any]) -> None:
        batch_id = str(frame.get("batch_id") or "")
        if frame.get("challenge"):
            self._challenge = str(frame["challenge"])
        batch = self._inflight.pop(batch_id, None)
        if batch is None:
            return

        by_id = {q.event["event_id"]: q for q in batch}
        settled = set(frame.get("accepted") or []) | set(frame.get("duplicates") or [])

        for receipt in frame.get("rejected") or []:
            eid = receipt.get("event_id")
            self.rejected_total += 1
            self._last_error = f"rejected {receipt.get('code')}: {receipt.get('message')}"
            log.warning(
                "fleet event rejected",
                extra={"event_id": eid, "code": receipt.get("code"),
                       "reason": receipt.get("message")},
            )
            if eid:
                settled.add(eid)

        retry_ids = {
            str(r["event_id"])
            for r in (frame.get("retryable") or [])
            if r.get("event_id") in by_id
        }
        requeue = [
            q for q in batch
            if q.event["event_id"] in retry_ids or q.event["event_id"] not in settled
        ]
        for q in reversed(requeue):
            self._spool.appendleft(q)


    async def _session(self) -> None:
        headers = {"Authorization": f"Bearer {self.cfg.telemetry_key}"}
        async with websockets.connect(
            self.cfg.ingest_url,
            additional_headers=headers,
            max_size=FRAME_BYTES_MAX * 2,
            open_timeout=15,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            self._gen += 1
            for batch in self._inflight.values():
                for q in reversed(batch):
                    self._spool.appendleft(q)
            self._inflight.clear()

            await ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "protocol_version": PROTOCOL_VERSION,
                        "node_id": self.cfg.node_id,
                        "source_id": self.cfg.source_id,
                        "manifest_revision": self._manifest_revision,
                        "boot_id": self.boot_id,
                    },
                    separators=(",", ":"),
                )
            )
            welcome_raw = await asyncio.wait_for(ws.recv(), timeout=HELLO_DEADLINE_S)
            welcome = json.loads(welcome_raw)
            log.info("fleet welcome", extra={"frame": welcome})
            if welcome.get("challenge"):
                self._challenge = str(welcome["challenge"])
            if welcome.get("manifest_revision"):
                self._manifest_revision = int(welcome["manifest_revision"])
            for key in ("expected_heartbeat_s", "heartbeat_s", "heartbeat_interval_s"):
                if welcome.get(key):
                    self._heartbeat_s = float(welcome[key])
                    break

            self._last_error = ""
            log.info(
                "fleet connected",
                extra={"node_id": self.cfg.node_id, "source_id": self.cfg.source_id,
                       "boot_id": self.boot_id, "revision": self._manifest_revision,
                       "heartbeat_s": self._heartbeat_s},
            )

            reader = asyncio.create_task(self._reader(ws))
            writer = asyncio.create_task(self._writer(ws))
            try:
                done, pending = await asyncio.wait(
                    {reader, writer}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    task.result()
            finally:
                for task in (reader, writer):
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

    async def _reader(self, ws: Any) -> None:
        async for raw in ws:
            try:
                frame = json.loads(raw)
            except (ValueError, TypeError):
                log.warning("fleet: undecodable frame")
                continue
            if frame.get("type") == "ack":
                self._handle_ack(frame)
            else:
                log.info("fleet frame", extra={"type": frame.get("type")})

    async def _writer(self, ws: Any) -> None:
        loop = asyncio.get_running_loop()
        next_heartbeat = loop.time()
        next_sample = loop.time()
        while True:
            now = loop.time()
            if now >= next_heartbeat:
                await self._send_heartbeat(ws)
                next_heartbeat = now + self._heartbeat_s
            if now >= next_sample:
                self._sample()
                next_sample = now + self.cfg.sample_every_s
            taken = self._take_batch()
            if taken is not None:
                await self._send_batch(ws, taken[0], taken[1])
                continue
            await asyncio.sleep(TICK_S)


    async def run(self) -> None:
        'Connect-and-publish forever, backing off across failures.'
        self.emit_lifecycle("starting", "publisher start")
        backoff = BACKOFF_MIN_S
        while not self._stopping:
            try:
                await self._session()
                backoff = BACKOFF_MIN_S
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - see the docstring
                self._last_error = f"{type(e).__name__}: {e}"[:4096]
                log.warning(
                    "fleet session ended; retrying",
                    extra={"error": self._last_error, "backoff_s": round(backoff, 1),
                           "spool_depth": len(self._spool)},
                )
            if self._stopping:
                break
            await asyncio.sleep(backoff * (0.5 + random.random()))
            backoff = min(backoff * 2, BACKOFF_MAX_S)

    async def stop(self, reason: str = "service shutdown") -> None:
        'Best-effort `stopping` event. Never blocks shutdown for long.'
        self._stopping = True
        self.emit_lifecycle("stopping", reason)


async def _supervise(publisher: FleetPublisher) -> None:
    try:
        await publisher.run()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - last line of defence
        log.exception("fleet publisher stopped unexpectedly")


def start(config: FleetConfig, collector: Collector) -> tuple[FleetPublisher, asyncio.Task[None]]:
    'Create the publisher and its supervised background task.'
    publisher = FleetPublisher(config, collector)
    return publisher, asyncio.create_task(_supervise(publisher))
