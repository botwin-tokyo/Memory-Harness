'What the harness reports to the fleet hub.'

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Any

from prometheus_client import REGISTRY

from memory_harness.config import Settings, get_settings
from memory_harness.db.connection import open_reader
from memory_harness.db.migrate import schema_version
from memory_harness.obs import metrics as m
from memory_harness.workers import queue
from memory_fleet.collector import HealthCheck, HealthSnapshot, MetricSample, rollup, summarize

EMBED_DEGRADED_AT = 100
EMBED_UNHEALTHY_AT = 1000
EXTRACT_DEGRADED_AT = 100
EXTRACT_UNHEALTHY_AT = 1000
FAILED_DEGRADED_AT = 1
FAILED_UNHEALTHY_AT = 25
WAL_DEGRADED_BYTES = 256 * 1024 * 1024

EXPECTED_EMOTIONS = 127

CHECK_NAMES = (
    "db",
    "emotions",
    "embed_queue",
    "extract_queue",
    "failed_jobs",
    "embed_worker",
    "extract_worker",
    "decay_worker",
)

METRIC_DECLARATIONS: dict[str, tuple[str, str]] = {
    "embed_queue_depth": ("jobs", "gauge"),
    "extract_queue_depth": ("jobs", "gauge"),
    "jobs_failed_depth": ("jobs", "gauge"),
    "db_wal_bytes": ("bytes", "gauge"),
    "db_bytes": ("bytes", "gauge"),
    "schema_version": ("version", "gauge"),
    "events_appended_total": ("events", "counter"),
    "writes_total": ("writes", "counter"),
    "disputes_total": ("writes", "counter"),
    "secrets_redacted_total": ("contents", "counter"),
    "append_latency_ms_sum": ("milliseconds", "counter"),
    "append_latency_ms_count": ("observations", "counter"),
    "recall_latency_ms_sum": ("milliseconds", "counter"),
    "recall_latency_ms_count": ("observations", "counter"),
    "embed_latency_ms_sum": ("milliseconds", "counter"),
    "embed_latency_ms_count": ("observations", "counter"),
}


def _registry_samples() -> dict[str, list[tuple[dict[str, str], float]]]:
    'Every sample in the default registry, keyed by sample name.'
    out: dict[str, list[tuple[dict[str, str], float]]] = {}
    for family in REGISTRY.collect():
        for sample in family.samples:
            out.setdefault(sample.name, []).append((dict(sample.labels), sample.value))
    return out


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _threshold_check(
    name: str, value: int, degraded_at: int, unhealthy_at: int
) -> HealthCheck:
    if value >= unhealthy_at:
        return HealthCheck(name, "unhealthy", f"{value}")
    if value >= degraded_at:
        return HealthCheck(name, "degraded", f"{value}")
    return HealthCheck(name, "healthy", f"{value}")


@dataclass
class HarnessCollector:
    settings: Settings
    workers: list[Any] | None = None
    worker_names: tuple[str, ...] = ()

    check_names: tuple[str, ...] = CHECK_NAMES
    metric_declarations: dict[str, tuple[str, str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.metric_declarations = METRIC_DECLARATIONS


    def _read(self) -> sqlite3.Connection:
        return open_reader(self.settings.db.path, self.settings.db.busy_timeout_ms)

    def _db_facts(self) -> dict[str, int]:
        con = self._read()
        try:
            cur = con.cursor()
            return {
                "schema_version": int(schema_version(con)),
                "emotions": int(
                    cur.execute("SELECT COUNT(*) FROM emotions").fetchone()[0]
                ),
                "embed_pending": queue.queue_depth(cur, "embed"),
                "extract_pending": queue.queue_depth(cur, "extract"),
                "failed": int(
                    cur.execute(
                        "SELECT COUNT(*) FROM jobs WHERE state='failed'"
                    ).fetchone()[0]
                ),
            }
        finally:
            con.close()


    def _worker_checks(self) -> list[HealthCheck]:
        'One check per background task, from `app.state.workers`.'
        if self.workers is None:
            return [HealthCheck(n, "unknown", "no app context") for n in
                    ("embed_worker", "extract_worker", "decay_worker")]
        alive = {
            name: not task.done()
            for name, task in zip(self.worker_names, self.workers)
        }
        checks = []
        for name in ("embed_worker", "extract_worker", "decay_worker"):
            if name not in alive:
                checks.append(HealthCheck(name, "unknown", "not started (no api key)"))
            else:
                checks.append(
                    HealthCheck(name, "healthy" if alive[name] else "unhealthy",
                                "running" if alive[name] else "task exited")
                )
        return checks

    def health(self) -> HealthSnapshot:
        try:
            facts = self._db_facts()
        except Exception as e:  # noqa: BLE001 - an unreadable DB IS the health report
            unreadable = tuple(
                [HealthCheck("db", "unhealthy", f"{type(e).__name__}: {e}")]
                + [HealthCheck(n, "unknown", "db unreadable")
                   for n in CHECK_NAMES if n != "db"]
            )
            return HealthSnapshot("unhealthy", summarize(unreadable), unreadable)

        checks: list[HealthCheck] = [
            HealthCheck("db", "healthy", f"schema {facts['schema_version']}"),
            HealthCheck(
                "emotions",
                "healthy" if facts["emotions"] == EXPECTED_EMOTIONS else "degraded",
                f"{facts['emotions']}/{EXPECTED_EMOTIONS}",
            ),
            _threshold_check("embed_queue", facts["embed_pending"],
                             EMBED_DEGRADED_AT, EMBED_UNHEALTHY_AT),
            _threshold_check("extract_queue", facts["extract_pending"],
                             EXTRACT_DEGRADED_AT, EXTRACT_UNHEALTHY_AT),
            _threshold_check("failed_jobs", facts["failed"],
                             FAILED_DEGRADED_AT, FAILED_UNHEALTHY_AT),
        ]
        checks.extend(self._worker_checks())
        frozen = tuple(checks)
        return HealthSnapshot(rollup(frozen), summarize(frozen), frozen)


    def metrics(self) -> list[MetricSample]:
        out: list[MetricSample] = []
        try:
            facts = self._db_facts()
        except Exception:  # noqa: BLE001 - health already reports this; emit no gauges
            facts = {}

        if facts:
            m.embed_queue_depth.set(facts["embed_pending"])
            m.extract_queue_depth.set(facts["extract_pending"])
            m.jobs_failed_depth.set(facts["failed"])
            out += [
                MetricSample("embed_queue_depth", facts["embed_pending"], "jobs"),
                MetricSample("extract_queue_depth", facts["extract_pending"], "jobs"),
                MetricSample("jobs_failed_depth", facts["failed"], "jobs"),
                MetricSample("schema_version", facts["schema_version"], "version"),
            ]

        db_path = self.settings.db.path
        out += [
            MetricSample("db_bytes", _file_size(db_path), "bytes"),
            MetricSample("db_wal_bytes", _file_size(db_path + "-wal"), "bytes"),
        ]

        samples = _registry_samples()
        for name in ("events_appended_total", "disputes_total", "secrets_redacted_total"):
            for labels, value in samples.get(name, []):
                out.append(MetricSample(name, value, METRIC_DECLARATIONS[name][0]))

        for labels, value in samples.get("writes_total", []):
            route = labels.get("route", "unknown")
            out.append(MetricSample("writes_total", value, "writes", {"route": route}))

        for base in ("append_latency_ms", "recall_latency_ms", "embed_latency_ms"):
            for suffix in ("_sum", "_count"):
                for _labels, value in samples.get(base + suffix, []):
                    out.append(
                        MetricSample(base + suffix, value,
                                     METRIC_DECLARATIONS[base + suffix][0])
                    )
        return out


def build_collector(
    settings: Settings | None = None,
    workers: list[Any] | None = None,
    worker_names: tuple[str, ...] = (),
) -> HarnessCollector:
    return HarnessCollector(
        settings=settings or get_settings(), workers=workers, worker_names=worker_names
    )
