from __future__ import annotations

from types import SimpleNamespace

from interview_analytics_agent.common import metrics


def test_refresh_connector_metrics_sets_gauges(monkeypatch) -> None:
    monkeypatch.setattr(
        "interview_analytics_agent.services.sberjazz_service.list_sberjazz_sessions",
        lambda limit=2000: [
            SimpleNamespace(connected=True),
            SimpleNamespace(connected=False),
            SimpleNamespace(connected=True),
        ],
    )
    monkeypatch.setattr(
        "interview_analytics_agent.services.sberjazz_service.get_sberjazz_connector_health",
        lambda: SimpleNamespace(healthy=True),
    )
    monkeypatch.setattr(
        "interview_analytics_agent.services.sberjazz_service.get_sberjazz_circuit_breaker_state",
        lambda: SimpleNamespace(state="closed"),
    )

    metrics.refresh_connector_metrics()

    connected = metrics.SBERJAZZ_SESSIONS_TOTAL.labels(state="connected")._value.get()
    disconnected = metrics.SBERJAZZ_SESSIONS_TOTAL.labels(state="disconnected")._value.get()
    healthy = metrics.SBERJAZZ_CONNECTOR_HEALTH._value.get()
    cb_open = metrics.SBERJAZZ_CIRCUIT_BREAKER_OPEN._value.get()

    assert connected == 2
    assert disconnected == 1
    assert healthy == 1
    assert cb_open == 0


def test_record_reconcile_metrics_sets_last_values() -> None:
    metrics.record_sberjazz_reconcile_result(
        source="job",
        stale=4,
        failed=1,
        reconnected=3,
    )

    stale = metrics.SBERJAZZ_RECONCILE_LAST_STALE._value.get()
    failed = metrics.SBERJAZZ_RECONCILE_LAST_FAILED._value.get()
    reconnected = metrics.SBERJAZZ_RECONCILE_LAST_RECONNECTED._value.get()

    assert stale == 4
    assert failed == 1
    assert reconnected == 3
