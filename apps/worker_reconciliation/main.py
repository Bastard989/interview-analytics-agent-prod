"""
Worker Reconciliation.

Назначение:
- периодически запускать reconciliation_job
- автоматически восстанавливать stale SberJazz-сессии
"""

from __future__ import annotations

import time

from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.common.logging import get_project_logger, setup_logging
from interview_analytics_agent.jobs.reconciliation_job import run as run_reconciliation
from interview_analytics_agent.services.readiness_service import enforce_startup_readiness

log = get_project_logger()


def main() -> None:
    setup_logging()
    enforce_startup_readiness(service_name="worker-reconciliation")
    settings = get_settings()
    interval_sec = max(5, int(settings.reconciliation_interval_sec))

    log.info(
        "worker_reconciliation_started",
        extra={
            "payload": {
                "enabled": bool(settings.reconciliation_enabled),
                "interval_sec": interval_sec,
                "limit": int(settings.reconciliation_limit),
                "live_pull_enabled": bool(settings.sberjazz_live_pull_enabled),
                "live_pull_batch_limit": int(settings.sberjazz_live_pull_batch_limit),
            }
        },
    )

    while True:
        try:
            run_reconciliation(limit=int(settings.reconciliation_limit))
        except Exception as e:
            log.error(
                "worker_reconciliation_error",
                extra={"payload": {"err": str(e)[:300]}},
            )
        time.sleep(interval_sec)


if __name__ == "__main__":
    main()
