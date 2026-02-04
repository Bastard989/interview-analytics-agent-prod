"""
Worker Retention.

Алгоритм (MVP):
- читаем из Redis Stream q:retention (consumer group)
- запускает apply_retention по БД (очистка текстов по cutoff)
- (позже) удаление объектов из локальное хранилище
"""

from __future__ import annotations

import time
from contextlib import suppress

from interview_analytics_agent.common.logging import get_project_logger, setup_logging
from interview_analytics_agent.common.metrics import QUEUE_TASKS_TOTAL, track_stage_latency
from interview_analytics_agent.queue.dispatcher import Q_RETENTION
from interview_analytics_agent.queue.retry import requeue_with_backoff
from interview_analytics_agent.queue.streams import ack_task, consumer_name, read_task
from interview_analytics_agent.storage.db import db_session
from interview_analytics_agent.storage.retention import apply_retention

log = get_project_logger()
GROUP_RETENTION = "g:retention"


def run_loop() -> None:
    consumer = consumer_name("worker-retention")
    log.info("worker_retention_started", extra={"payload": {"queue": Q_RETENTION}})

    while True:
        msg = read_task(
            stream=Q_RETENTION, group=GROUP_RETENTION, consumer=consumer, block_ms=10000
        )
        if not msg:
            continue

        should_ack = False
        try:
            with track_stage_latency("worker-retention", "retention"):
                task = msg.payload

                with db_session() as session:
                    apply_retention(session)

                log.info(
                    "retention_applied",
                    extra={
                        "payload": {
                            "task": {
                                "entity_type": task.get("entity_type"),
                                "entity_id": task.get("entity_id"),
                            }
                        }
                    },
                )
            should_ack = True
            QUEUE_TASKS_TOTAL.labels(
                service="worker-retention", queue=Q_RETENTION, result="success"
            ).inc()

        except Exception as e:
            log.error(
                "worker_retention_error",
                extra={
                    "payload": {"err": str(e)[:200], "task": task if "task" in locals() else None}
                },
            )
            QUEUE_TASKS_TOTAL.labels(
                service="worker-retention", queue=Q_RETENTION, result="error"
            ).inc()
            try:
                task = task if "task" in locals() else {}
                requeue_with_backoff(
                    queue_name=Q_RETENTION, task_payload=task, max_attempts=3, backoff_sec=3
                )
                should_ack = True
                QUEUE_TASKS_TOTAL.labels(
                    service="worker-retention", queue=Q_RETENTION, result="retry"
                ).inc()
            except Exception:
                pass
        finally:
            if should_ack:
                with suppress(Exception):
                    ack_task(stream=Q_RETENTION, group=GROUP_RETENTION, entry_id=msg.entry_id)


def main() -> None:
    setup_logging()
    while True:
        try:
            run_loop()
        except Exception as e:
            log.error("worker_retention_fatal", extra={"payload": {"err": str(e)[:200]}})
            time.sleep(2)


if __name__ == "__main__":
    main()
