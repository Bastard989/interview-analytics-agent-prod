"""
Диспетчер очередей.

Назначение:
- Единые имена очередей
- Унифицированная упаковка задач в JSON
- Удобные функции enqueue_* для всех стадий пайплайна
"""

from __future__ import annotations

from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.common.ids import new_event_id
from interview_analytics_agent.common.logging import get_project_logger
from interview_analytics_agent.common.time import utc_now_iso
from interview_analytics_agent.common.tracing import inject_trace_context
from interview_analytics_agent.services.local_pipeline import process_chunk_inline

from .streams import enqueue

log = get_project_logger()

# =============================================================================
# ИМЕНА ОЧЕРЕДЕЙ (Redis Streams)
# =============================================================================
Q_STT = "q:stt"
Q_ENHANCER = "q:enhancer"
Q_ANALYTICS = "q:analytics"
Q_DELIVERY = "q:delivery"
Q_RETENTION = "q:retention"

# DLQ
Q_DLQ = "q:dlq"


def _now_iso() -> str:
    return utc_now_iso()


def enqueue_stt(*, meeting_id: str, chunk_seq: int, blob_key: str) -> str:
    """
    Поставить задачу STT на обработку аудио-чанка.
    """
    event_id = new_event_id("stt")
    payload = {
        "schema_version": "v1",
        "event_id": event_id,
        "meeting_id": meeting_id,
        "chunk_seq": chunk_seq,
        "blob_key": blob_key,
        "timestamp": _now_iso(),
    }
    inject_trace_context(payload, meeting_id=meeting_id, source="queue.stt")
    if (get_settings().queue_mode or "").strip().lower() == "inline":
        process_chunk_inline(meeting_id=meeting_id, chunk_seq=chunk_seq, blob_key=blob_key)
        log.info(
            "enqueue_stt_inline",
            extra={"payload": {"meeting_id": meeting_id, "chunk_seq": chunk_seq, "event_id": event_id}},
        )
        return event_id

    enqueue(Q_STT, payload)
    log.info(
        "enqueue_stt",
        extra={"payload": {"meeting_id": meeting_id, "chunk_seq": chunk_seq, "event_id": event_id}},
    )
    return event_id


def enqueue_enhancer(*, meeting_id: str) -> str:
    """
    Поставить задачу улучшения текста.
    """
    event_id = new_event_id("enh")
    payload = {"schema_version": "v1", "event_id": event_id, "meeting_id": meeting_id}
    inject_trace_context(payload, meeting_id=meeting_id, source="queue.enhancer")
    enqueue(Q_ENHANCER, payload)
    log.info(
        "enqueue_enhancer",
        extra={"payload": {"meeting_id": meeting_id, "event_id": event_id}},
    )
    return event_id


def enqueue_analytics(*, meeting_id: str) -> str:
    """
    Поставить задачу аналитики/отчёта.
    """
    event_id = new_event_id("anl")
    payload = {"schema_version": "v1", "event_id": event_id, "meeting_id": meeting_id}
    inject_trace_context(payload, meeting_id=meeting_id, source="queue.analytics")
    enqueue(Q_ANALYTICS, payload)
    log.info(
        "enqueue_analytics",
        extra={"payload": {"meeting_id": meeting_id, "event_id": event_id}},
    )
    return event_id


def enqueue_delivery(*, meeting_id: str) -> str:
    """
    Поставить задачу доставки результатов.
    """
    event_id = new_event_id("dlv")
    payload = {"schema_version": "v1", "event_id": event_id, "meeting_id": meeting_id}
    inject_trace_context(payload, meeting_id=meeting_id, source="queue.delivery")
    enqueue(Q_DELIVERY, payload)
    log.info(
        "enqueue_delivery",
        extra={"payload": {"meeting_id": meeting_id, "event_id": event_id}},
    )
    return event_id


def enqueue_retention(*, entity_type: str, entity_id: str, reason: str) -> str:
    """
    Поставить задачу ретеншна (очистки).
    """
    event_id = new_event_id("ret")
    payload = {
        "schema_version": "v1",
        "event_id": event_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "reason": reason,
    }
    inject_trace_context(payload, source="queue.retention")
    enqueue(Q_RETENTION, payload)
    log.info("enqueue_retention", extra={"payload": payload})
    return event_id
