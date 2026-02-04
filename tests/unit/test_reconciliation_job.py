from __future__ import annotations

from types import SimpleNamespace

from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.jobs import reconciliation_job


def test_reconciliation_job_skips_when_disabled() -> None:
    s = get_settings()
    snapshot_enabled = s.reconciliation_enabled
    try:
        s.reconciliation_enabled = False
        result = reconciliation_job.run(limit=10)
        assert result is None
    finally:
        s.reconciliation_enabled = snapshot_enabled


def test_reconciliation_job_runs_with_limit(monkeypatch) -> None:
    s = get_settings()
    snapshot_enabled = s.reconciliation_enabled
    snapshot_limit = s.reconciliation_limit
    calls: list[int] = []

    def _fake_reconcile(limit: int):
        calls.append(limit)
        return SimpleNamespace(
            scanned=1,
            stale=1,
            reconnected=1,
            failed=0,
            stale_threshold_sec=900,
            updated_at="2026-02-04T00:00:00+00:00",
        )

    monkeypatch.setattr(reconciliation_job, "reconcile_sberjazz_sessions", _fake_reconcile)
    monkeypatch.setattr(
        reconciliation_job,
        "pull_sberjazz_live_chunks",
        lambda **kwargs: SimpleNamespace(
            scanned=0,
            connected=0,
            pulled=0,
            ingested=0,
            failed=0,
            updated_at="2026-02-04T00:00:00+00:00",
        ),
    )
    try:
        s.reconciliation_enabled = True
        s.reconciliation_limit = 123
        result = reconciliation_job.run()
        assert result is not None
        assert calls == [123]
    finally:
        s.reconciliation_enabled = snapshot_enabled
        s.reconciliation_limit = snapshot_limit


def test_reconciliation_job_auto_resets_cb_when_healthy(monkeypatch) -> None:
    s = get_settings()
    snapshot_enabled = s.reconciliation_enabled
    snapshot_limit = s.reconciliation_limit
    snapshot_auto = s.sberjazz_cb_auto_reset_enabled
    snapshot_min_age = s.sberjazz_cb_auto_reset_min_age_sec
    reset_calls: list[str] = []

    monkeypatch.setattr(
        reconciliation_job,
        "get_sberjazz_circuit_breaker_state",
        lambda: SimpleNamespace(
            state="open",
            consecutive_failures=3,
            opened_at="2020-01-01T00:00:00+00:00",
            last_error="timeout",
            updated_at="2020-01-01T00:00:00+00:00",
        ),
    )
    monkeypatch.setattr(
        reconciliation_job,
        "get_sberjazz_connector_health",
        lambda: SimpleNamespace(healthy=True, details={}),
    )
    monkeypatch.setattr(
        reconciliation_job,
        "reset_sberjazz_circuit_breaker",
        lambda reason: reset_calls.append(reason),
    )
    monkeypatch.setattr(
        reconciliation_job,
        "reconcile_sberjazz_sessions",
        lambda limit: SimpleNamespace(
            scanned=1,
            stale=0,
            reconnected=0,
            failed=0,
            stale_threshold_sec=900,
            updated_at="2026-02-04T00:00:00+00:00",
        ),
    )
    monkeypatch.setattr(
        reconciliation_job,
        "pull_sberjazz_live_chunks",
        lambda **kwargs: SimpleNamespace(
            scanned=0,
            connected=0,
            pulled=0,
            ingested=0,
            failed=0,
            updated_at="2026-02-04T00:00:00+00:00",
        ),
    )
    try:
        s.reconciliation_enabled = True
        s.reconciliation_limit = 5
        s.sberjazz_cb_auto_reset_enabled = True
        s.sberjazz_cb_auto_reset_min_age_sec = 0
        reconciliation_job.run()
        assert reset_calls == ["auto_health_recovered"]
    finally:
        s.reconciliation_enabled = snapshot_enabled
        s.reconciliation_limit = snapshot_limit
        s.sberjazz_cb_auto_reset_enabled = snapshot_auto
        s.sberjazz_cb_auto_reset_min_age_sec = snapshot_min_age


def test_reconciliation_job_does_not_reset_cb_when_unhealthy(monkeypatch) -> None:
    s = get_settings()
    snapshot_enabled = s.reconciliation_enabled
    snapshot_limit = s.reconciliation_limit
    snapshot_auto = s.sberjazz_cb_auto_reset_enabled
    snapshot_min_age = s.sberjazz_cb_auto_reset_min_age_sec
    reset_calls: list[str] = []

    monkeypatch.setattr(
        reconciliation_job,
        "get_sberjazz_circuit_breaker_state",
        lambda: SimpleNamespace(
            state="open",
            consecutive_failures=3,
            opened_at="2020-01-01T00:00:00+00:00",
            last_error="timeout",
            updated_at="2020-01-01T00:00:00+00:00",
        ),
    )
    monkeypatch.setattr(
        reconciliation_job,
        "get_sberjazz_connector_health",
        lambda: SimpleNamespace(healthy=False, details={"error": "provider_down"}),
    )
    monkeypatch.setattr(
        reconciliation_job,
        "reset_sberjazz_circuit_breaker",
        lambda reason: reset_calls.append(reason),
    )
    monkeypatch.setattr(
        reconciliation_job,
        "reconcile_sberjazz_sessions",
        lambda limit: SimpleNamespace(
            scanned=1,
            stale=0,
            reconnected=0,
            failed=0,
            stale_threshold_sec=900,
            updated_at="2026-02-04T00:00:00+00:00",
        ),
    )
    monkeypatch.setattr(
        reconciliation_job,
        "pull_sberjazz_live_chunks",
        lambda **kwargs: SimpleNamespace(
            scanned=0,
            connected=0,
            pulled=0,
            ingested=0,
            failed=0,
            updated_at="2026-02-04T00:00:00+00:00",
        ),
    )
    try:
        s.reconciliation_enabled = True
        s.reconciliation_limit = 5
        s.sberjazz_cb_auto_reset_enabled = True
        s.sberjazz_cb_auto_reset_min_age_sec = 0
        reconciliation_job.run()
        assert reset_calls == []
    finally:
        s.reconciliation_enabled = snapshot_enabled
        s.reconciliation_limit = snapshot_limit
        s.sberjazz_cb_auto_reset_enabled = snapshot_auto
        s.sberjazz_cb_auto_reset_min_age_sec = snapshot_min_age


def test_reconciliation_job_skips_live_pull_when_disabled(monkeypatch) -> None:
    s = get_settings()
    snapshot_enabled = s.reconciliation_enabled
    snapshot_live = s.sberjazz_live_pull_enabled
    live_calls: list[int] = []

    monkeypatch.setattr(
        reconciliation_job,
        "reconcile_sberjazz_sessions",
        lambda limit: SimpleNamespace(
            scanned=1,
            stale=0,
            reconnected=0,
            failed=0,
            stale_threshold_sec=900,
            updated_at="2026-02-04T00:00:00+00:00",
        ),
    )
    monkeypatch.setattr(
        reconciliation_job,
        "pull_sberjazz_live_chunks",
        lambda **kwargs: live_calls.append(1),
    )
    try:
        s.reconciliation_enabled = True
        s.sberjazz_live_pull_enabled = False
        reconciliation_job.run(limit=5)
        assert live_calls == []
    finally:
        s.reconciliation_enabled = snapshot_enabled
        s.sberjazz_live_pull_enabled = snapshot_live
