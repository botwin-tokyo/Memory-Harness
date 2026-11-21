from __future__ import annotations

import pytest

from memory_fleet.collector import HealthCheck, HealthSnapshot, MetricSample
from memory_fleet.config import FleetConfig


class FakeCollector:
    'A collector with dials the tests can turn.'

    check_names = ("alpha", "beta")
    metric_declarations = {"widgets": ("widgets", "gauge"), "hits": ("hits", "counter")}

    def __init__(self) -> None:
        self.status = "healthy"
        self.value = 1.0
        self.raise_health = False
        self.raise_metrics = False

    def health(self) -> HealthSnapshot:
        if self.raise_health:
            raise RuntimeError("boom")
        checks = (
            HealthCheck("alpha", self.status),
            HealthCheck("beta", "healthy"),
        )
        return HealthSnapshot(self.status, "fake", checks)

    def metrics(self) -> list[MetricSample]:
        if self.raise_metrics:
            raise RuntimeError("boom")
        return [
            MetricSample("widgets", self.value, "widgets"),
            MetricSample("hits", 7, "hits"),
        ]


@pytest.fixture
def collector() -> FakeCollector:
    return FakeCollector()


@pytest.fixture
def config() -> FleetConfig:
    return FleetConfig(
        node_id="memory-node",
        source_id="harness",
        telemetry_key="test-key",
        heartbeat_s=5,
        spool_max=10,
        sample_every_s=1.0,
    )
