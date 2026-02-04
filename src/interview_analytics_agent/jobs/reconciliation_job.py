"""
Reconciliation job.

Назначение:
- поддержание коннектора в устойчивом состоянии
- поиск stale-сессий и авто-reconnect
"""

from __future__ import annotations

from datetime import UTC, datetime

from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.common.logging import get_project_logger
from interview_analytics_agent.common.metrics import (
    record_sberjazz_cb_reset,
    record_sberjazz_reconcile_result,
)
from interview_analytics_agent.services.sberjazz_service import (
    SberJazzReconcileResult,
    get_sberjazz_circuit_breaker_state,
    get_sberjazz_connector_health,
    pull_sberjazz_live_chunks,
    reconcile_sberjazz_sessions,
    reset_sberjazz_circuit_breaker,
)

log = get_project_logger()


def _parse_opened_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _maybe_auto_reset_circuit_breaker() -> None:
    settings = get_settings()
    if not bool(getattr(settings, "sberjazz_cb_auto_reset_enabled", True)):
        return

    state = get_sberjazz_circuit_breaker_state()
    if state.state != "open":
        return

    opened = _parse_opened_at(state.opened_at)
    min_age_sec = max(0, int(getattr(settings, "sberjazz_cb_auto_reset_min_age_sec", 30)))
    if opened is not None:
        age_sec = max(0, int((datetime.now(UTC) - opened).total_seconds()))
        if age_sec < min_age_sec:
            log.info(
                "reconciliation_cb_auto_reset_skipped",
                extra={
                    "payload": {
                        "reason": "min_age_not_reached",
                        "age_sec": age_sec,
                        "min_age_sec": min_age_sec,
                    }
                },
            )
            return

    health = get_sberjazz_connector_health()
    if not health.healthy:
        log.info(
            "reconciliation_cb_auto_reset_skipped",
            extra={"payload": {"reason": "connector_unhealthy", "details": health.details}},
        )
        return

    reset_sberjazz_circuit_breaker(reason="auto_health_recovered")
    record_sberjazz_cb_reset(source="auto", reason="health_recovered")
    log.info("reconciliation_cb_auto_reset_done")


def _maybe_pull_live_chunks(*, reconcile_limit: int) -> None:
    settings = get_settings()
    if not bool(getattr(settings, "sberjazz_live_pull_enabled", True)):
        return

    sessions_limit = max(
        1,
        int(
            getattr(
                settings,
                "sberjazz_live_pull_sessions_limit",
                reconcile_limit,
            )
        ),
    )
    batch_limit = max(1, int(getattr(settings, "sberjazz_live_pull_batch_limit", 20)))
    result = pull_sberjazz_live_chunks(limit_sessions=sessions_limit, batch_limit=batch_limit)
    log.info(
        "reconciliation_live_pull_finished",
        extra={
            "payload": {
                "scanned": result.scanned,
                "connected": result.connected,
                "pulled": result.pulled,
                "ingested": result.ingested,
                "failed": result.failed,
            }
        },
    )


def run(*, limit: int | None = None) -> SberJazzReconcileResult | None:
    settings = get_settings()
    if not settings.reconciliation_enabled:
        log.info("reconciliation_job_skipped", extra={"payload": {"reason": "disabled"}})
        return None

    reconcile_limit = int(limit if limit is not None else settings.reconciliation_limit)
    log.info("reconciliation_job_started", extra={"payload": {"limit": reconcile_limit}})

    try:
        _maybe_auto_reset_circuit_breaker()
    except Exception as e:
        log.warning(
            "reconciliation_cb_auto_reset_failed",
            extra={"payload": {"err": str(e)[:300]}},
        )

    result = reconcile_sberjazz_sessions(limit=max(1, reconcile_limit))
    record_sberjazz_reconcile_result(
        source="job",
        stale=result.stale,
        failed=result.failed,
        reconnected=result.reconnected,
    )
    try:
        _maybe_pull_live_chunks(reconcile_limit=reconcile_limit)
    except Exception as e:
        log.warning(
            "reconciliation_live_pull_failed",
            extra={"payload": {"err": str(e)[:300]}},
        )
    log.info(
        "reconciliation_job_finished",
        extra={
            "payload": {
                "scanned": result.scanned,
                "stale": result.stale,
                "reconnected": result.reconnected,
                "failed": result.failed,
            }
        },
    )
    return result
