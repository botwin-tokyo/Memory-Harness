'What a service has to implement to be monitorable: two methods.'

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from memory_fleet.design import HEALTH_STATUSES

log = logging.getLogger("memory_fleet.collector")

Status = str


@dataclass(frozen=True)
class HealthCheck:

    name: str
    status: Status
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in HEALTH_STATUSES:
            raise ValueError(f"bad status {self.status!r}, want one of {HEALTH_STATUSES}")


@dataclass(frozen=True)
class MetricSample:
    name: str
    value: float
    unit: str
    labels: dict[str, str] = field(default_factory=dict)
    kind: str = "gauge"


@dataclass(frozen=True)
class HealthSnapshot:
    status: Status
    summary: str
    checks: tuple[HealthCheck, ...]


@runtime_checkable
class Collector(Protocol):
    "A service's telemetry surface."

    check_names: tuple[str, ...]
    metric_declarations: dict[str, tuple[str, str]]

    def health(self) -> HealthSnapshot: ...

    def metrics(self) -> list[MetricSample]: ...


def rollup(checks: tuple[HealthCheck, ...]) -> Status:
    'Worst-wins, with `unknown` ranked between degraded and unhealthy.'
    if not checks:
        return "unknown"
    order = {"healthy": 0, "degraded": 1, "unknown": 2, "unhealthy": 3}
    worst = max(checks, key=lambda c: order[c.status])
    return worst.status


def summarize(checks: tuple[HealthCheck, ...]) -> str:
    'One line naming what is not healthy, or that everything is.'
    bad = [c for c in checks if c.status != "healthy"]
    if not bad:
        return f"{len(checks)} checks healthy"
    return "; ".join(f"{c.status}: {c.name}" for c in bad)[:4096]


def safe_snapshot(collector: Collector) -> HealthSnapshot:
    '`collector.health()` with the must-not-raise guarantee enforced.'
    try:
        snap = collector.health()
    except Exception as e:  # noqa: BLE001 - a broken check is a state, not a crash
        log.warning("health collector raised", exc_info=True)
        checks = tuple(
            HealthCheck(name=n, status="unknown") for n in collector.check_names
        )
        return HealthSnapshot(
            status="unknown", summary=f"collector error: {type(e).__name__}", checks=checks
        )
    return snap


def safe_metrics(collector: Collector) -> list[MetricSample]:
    try:
        return collector.metrics()
    except Exception:  # noqa: BLE001
        log.warning("metric collector raised", exc_info=True)
        return []
