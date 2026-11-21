def test_healthz(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["schema_version"] == 14
    assert body["emotions"] == 127


def test_metrics_prometheus_text(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    for name in (
        "events_appended_total",
        "writes_total",
        "disputes_total",
        "secrets_redacted_total",
        "append_latency_ms",
        "recall_latency_ms",
        "embed_latency_ms",
        "embed_queue_depth",
        "extract_queue_depth",
    ):
        assert name in r.text
