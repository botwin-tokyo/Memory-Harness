from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

events_appended_total = Counter(
    "events_appended_total", "Events appended to the log (excludes deduped retries)"
)
writes_total = Counter("writes_total", "Durable writes by route", ["route"])
disputes_total = Counter("disputes_total", "Writes stored as disputed (trust gate)")
secrets_redacted_total = Counter(
    "secrets_redacted_total", "Contents redacted by the capture-time secret scan"
)

append_latency_ms = Histogram("append_latency_ms", "POST /v1/events append latency (ms)")
recall_latency_ms = Histogram("recall_latency_ms", "POST /v1/recall latency (ms)")
embed_latency_ms = Histogram("embed_latency_ms", "Embedding endpoint call latency (ms)")

embed_queue_depth = Gauge("embed_queue_depth", "Pending embed jobs in the jobs table")
extract_queue_depth = Gauge("extract_queue_depth", "Pending extract jobs in the jobs table")
jobs_failed_depth = Gauge("jobs_failed_depth", "Jobs parked in state='failed'")


def render() -> tuple[bytes, str]:
    'Prometheus text exposition + content type for the /metrics route.'
    return generate_latest(), CONTENT_TYPE_LATEST
