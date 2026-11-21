'What the MCP adapter reports to the fleet hub.'

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import httpx

from memory_fleet.collector import (
    HealthCheck,
    HealthSnapshot,
    MetricSample,
    rollup,
    summarize,
)

PROBE_TIMEOUT_S = 3.0

CHECK_NAMES = ("harness_reachable",)

METRIC_DECLARATIONS: dict[str, tuple[str, str]] = {
    "mcp_requests_total": ("requests", "counter"),
    "mcp_unauthorized_total": ("requests", "counter"),
}


class _Counters:
    'Process-local counters.'

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests = 0
        self.unauthorized = 0

    def request(self) -> None:
        with self._lock:
            self.requests += 1

    def unauthorized_request(self) -> None:
        with self._lock:
            self.unauthorized += 1


COUNTERS = _Counters()


@dataclass
class McpCollector:
    harness_healthz: str

    check_names: tuple[str, ...] = CHECK_NAMES
    metric_declarations: dict[str, tuple[str, str]] = field(
        default_factory=lambda: dict(METRIC_DECLARATIONS)
    )

    def health(self) -> HealthSnapshot:
        try:
            resp = httpx.get(self.harness_healthz, timeout=PROBE_TIMEOUT_S)
            ok, detail = resp.status_code < 500, f"{resp.status_code}"
        except Exception as e:  # noqa: BLE001 - unreachable IS the answer
            ok, detail = False, type(e).__name__
        checks = (
            HealthCheck("harness_reachable", "healthy" if ok else "unhealthy", detail),
        )
        return HealthSnapshot(rollup(checks), summarize(checks), checks)

    def metrics(self) -> list[MetricSample]:
        return [
            MetricSample("mcp_requests_total", COUNTERS.requests, "requests"),
            MetricSample("mcp_unauthorized_total", COUNTERS.unauthorized, "requests"),
        ]


def build_collector() -> McpCollector:
    from memory_mcp.config import get_settings

    settings = get_settings()
    return McpCollector(harness_healthz=settings.harness_base.rstrip("/") + "/healthz")
