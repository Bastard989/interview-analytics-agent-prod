"""
Worker Delivery.

Алгоритм (MVP):
- читаем из Redis Stream q:delivery (consumer group)
- читает Meeting + report
- рендерит шаблоны Jinja2
- отправляет через SMTP (если настроено)
- обновляет статус (в MVP: логируем и меняем статус встречи на done)
"""

from __future__ import annotations

import time
from contextlib import suppress
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.common.logging import get_project_logger, setup_logging
from interview_analytics_agent.common.metrics import QUEUE_TASKS_TOTAL, track_stage_latency
from interview_analytics_agent.delivery.email.sender import SMTPEmailProvider
from interview_analytics_agent.domain.enums import PipelineStatus
from interview_analytics_agent.queue.dispatcher import Q_DELIVERY, enqueue_retention
from interview_analytics_agent.queue.retry import requeue_with_backoff
from interview_analytics_agent.queue.streams import ack_task, consumer_name, read_task
from interview_analytics_agent.storage.db import db_session
from interview_analytics_agent.storage.repositories import MeetingRepository

log = get_project_logger()
GROUP_DELIVERY = "g:delivery"


def _jinja() -> Environment:
    tpl_dir = Path("src/interview_analytics_agent/delivery/email/templates")
    return Environment(
        loader=FileSystemLoader(str(tpl_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )


def run_loop() -> None:
    settings = get_settings()
    consumer = consumer_name("worker-delivery")
    env = _jinja()
    smtp = SMTPEmailProvider()

    log.info("worker_delivery_started", extra={"payload": {"queue": Q_DELIVERY}})

    while True:
        msg = read_task(stream=Q_DELIVERY, group=GROUP_DELIVERY, consumer=consumer, block_ms=5000)
        if not msg:
            continue

        should_ack = False
        try:
            with track_stage_latency("worker-delivery", "delivery"):
                task = msg.payload
                meeting_id = task["meeting_id"]

                with db_session() as session:
                    mrepo = MeetingRepository(session)
                    m = mrepo.get(meeting_id)

                    report = (m.report if m else None) or {
                        "summary": "",
                        "bullets": [],
                        "risk_flags": [],
                        "recommendation": "",
                    }
                    recipients = []
                    if m and isinstance(m.context, dict):
                        # Если ты захочешь — потом положим recipients в context при /meetings/start
                        recipients = m.context.get("recipients", []) or []

                    html = env.get_template("report.html.j2").render(
                        meeting_id=meeting_id, report=report
                    )
                    txt = env.get_template("report.txt.j2").render(
                        meeting_id=meeting_id, report=report
                    )

                    if settings.delivery_provider == "email" and recipients:
                        smtp.send_report(
                            meeting_id=meeting_id,
                            recipients=recipients,
                            subject=f"Отчёт по встрече {meeting_id}",
                            html_body=html,
                            text_body=txt,
                            attachments=None,
                        )
                        log.info(
                            "delivery_done",
                            extra={"meeting_id": meeting_id, "payload": {"recipients": recipients}},
                        )
                    else:
                        # В MVP, если нет получателей — считаем доставку пропущенной
                        log.warning(
                            "delivery_skipped",
                            extra={
                                "meeting_id": meeting_id,
                                "payload": {
                                    "provider": settings.delivery_provider,
                                    "recipients": recipients,
                                },
                            },
                        )

                    if m:
                        m.status = PipelineStatus.done
                        mrepo.save(m)

                enqueue_retention(
                    entity_type="meeting", entity_id=meeting_id, reason="delivered_or_skipped"
                )
            should_ack = True
            QUEUE_TASKS_TOTAL.labels(
                service="worker-delivery", queue=Q_DELIVERY, result="success"
            ).inc()

        except Exception as e:
            log.error(
                "worker_delivery_error",
                extra={
                    "payload": {"err": str(e)[:200], "task": task if "task" in locals() else None}
                },
            )
            QUEUE_TASKS_TOTAL.labels(
                service="worker-delivery", queue=Q_DELIVERY, result="error"
            ).inc()
            try:
                task = task if "task" in locals() else {}
                requeue_with_backoff(
                    queue_name=Q_DELIVERY, task_payload=task, max_attempts=3, backoff_sec=2
                )
                should_ack = True
                QUEUE_TASKS_TOTAL.labels(
                    service="worker-delivery", queue=Q_DELIVERY, result="retry"
                ).inc()
            except Exception:
                pass
        finally:
            if should_ack:
                with suppress(Exception):
                    ack_task(stream=Q_DELIVERY, group=GROUP_DELIVERY, entry_id=msg.entry_id)


def main() -> None:
    setup_logging()
    while True:
        try:
            run_loop()
        except Exception as e:
            log.error("worker_delivery_fatal", extra={"payload": {"err": str(e)[:200]}})
            time.sleep(2)


if __name__ == "__main__":
    main()
