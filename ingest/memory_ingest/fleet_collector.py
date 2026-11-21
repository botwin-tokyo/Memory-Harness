'What the ingestion service reports to the fleet hub.'

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import httpx

from memory_fleet.collector import (
    HealthCheck,
    HealthSnapshot,
    MetricSample,
    rollup,
    summarize,
)

PROBE_TIMEOUT_S = 3.0
QUEUE_DEGRADED_AT = 5
QUEUE_UNHEALTHY_AT = 25

CHECK_NAMES = ("harness_reachable", "ocr_reachable", "queue")

METRIC_DECLARATIONS: dict[str, tuple[str, str]] = {
    "ingest_queue_depth": ("jobs", "gauge"),
    "ingest_busy": ("boolean", "gauge"),
}


def _probe(url: str) -> tuple[str, str]:
    'Probe a liveness endpoint. Returns (status, detail).'
    try:
        resp = httpx.get(url, timeout=PROBE_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - unreachable IS the answer
        return "unhealthy", type(e).__name__

    if resp.status_code >= 500:
        return "unhealthy", f"{resp.status_code}"
    if resp.status_code != 200:
        return "unknown", f"{resp.status_code} (wrong path?)"

    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return "healthy", "200"
    reported = str(body.get("status", "ok"))
    if reported in ("ok", "healthy"):
        return "healthy", "200"
    return "degraded", reported


@dataclass
class IngestCollector:
    queue_depth: Callable[[], int]
    busy: Callable[[], bool]
    harness_healthz: str
    ocr_health: str

    check_names: tuple[str, ...] = CHECK_NAMES
    metric_declarations: dict[str, tuple[str, str]] = field(
        default_factory=lambda: dict(METRIC_DECLARATIONS)
    )

    def health(self) -> HealthSnapshot:
        harness_status, harness_detail = _probe(self.harness_healthz)
        ocr_status, ocr_detail = _probe(self.ocr_health)
        depth = self.queue_depth()

        if depth >= QUEUE_UNHEALTHY_AT:
            queue_status = "unhealthy"
        elif depth >= QUEUE_DEGRADED_AT:
            queue_status = "degraded"
        else:
            queue_status = "healthy"

        checks = (
            HealthCheck("harness_reachable", harness_status, harness_detail),
            HealthCheck(
                "ocr_reachable",
                "degraded" if ocr_status == "unhealthy" else ocr_status,
                ocr_detail,
            ),
            HealthCheck("queue", queue_status, f"{depth} waiting"),
        )
        return HealthSnapshot(rollup(checks), summarize(checks), checks)

    def metrics(self) -> list[MetricSample]:
        return [
            MetricSample("ingest_queue_depth", self.queue_depth(), "jobs"),
            MetricSample("ingest_busy", 1.0 if self.busy() else 0.0, "boolean"),
        ]


def build_collector() -> IngestCollector:
    "Bind the collector to the running server module's live counters."
    from memory_ingest.config import get_settings

    settings = get_settings()

    def _depth() -> int:
        from memory_ingest import server

        return max(server._queue_depth - 1, 0)

    def _busy() -> bool:
        from memory_ingest import server

        return bool(server._one_at_a_time.locked())

    return IngestCollector(
        queue_depth=_depth,
        busy=_busy,
        harness_healthz=settings.harness_base.rstrip("/") + "/healthz",
        ocr_health=settings.ocr_base.rstrip("/") + "/health",
    )
