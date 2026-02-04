"""
Worker STT.

Алгоритм:
- читаем из Redis Stream q:stt (consumer group)
- читаем аудио из локальное хранилище
- распознаём (локальный whisper по умолчанию)
- сохраняем TranscriptSegment (raw_text/enhanced_text=raw на старте)
- публикуем transcript.update в Redis pubsub ws:<meeting_id>
- ставим задачу enhancer

Важно:
- это MVP: один чанк -> один сегмент (seq)
"""

from __future__ import annotations

import json
import time
from contextlib import suppress

from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.common.logging import get_project_logger, setup_logging
from interview_analytics_agent.common.metrics import QUEUE_TASKS_TOTAL, track_stage_latency
from interview_analytics_agent.domain.enums import PipelineStatus
from interview_analytics_agent.queue.dispatcher import Q_STT, enqueue_enhancer
from interview_analytics_agent.queue.redis import redis_client
from interview_analytics_agent.queue.retry import requeue_with_backoff
from interview_analytics_agent.queue.streams import ack_task, consumer_name, read_task
from interview_analytics_agent.storage.blob import get_bytes
from interview_analytics_agent.storage.db import db_session
from interview_analytics_agent.storage.models import TranscriptSegment
from interview_analytics_agent.storage.repositories import (
    MeetingRepository,
    TranscriptSegmentRepository,
)
from interview_analytics_agent.stt.mock import MockSTTProvider

log = get_project_logger()

GROUP_STT = "g:stt"


def _build_stt_provider():
    s = get_settings()

    if s.stt_provider == "mock":
        return MockSTTProvider()

    if s.stt_provider == "google":
        from interview_analytics_agent.stt.google import GoogleSTTProvider

        return GoogleSTTProvider()

    if s.stt_provider == "salutespeech":
        from interview_analytics_agent.stt.salutespeech import SaluteSpeechProvider

        return SaluteSpeechProvider()

    # default: whisper_local (тяжёлые зависимости импортируем только тут)
    from interview_analytics_agent.stt.whisper_local import WhisperLocalProvider

    return WhisperLocalProvider(
        model_size=s.whisper_model_size,
        device=s.whisper_device,
        compute_type=s.whisper_compute_type,
        language=s.whisper_language,
        vad_filter=s.whisper_vad_filter,
        beam_size=s.whisper_beam_size,
    )


def _publish_update(meeting_id: str, payload: dict) -> None:
    redis_client().publish(f"ws:{meeting_id}", json.dumps(payload, ensure_ascii=False))


def run_loop() -> None:
    s = get_settings()
    stt = _build_stt_provider()
    consumer = consumer_name("worker-stt")

    log.info("worker_stt_started", extra={"payload": {"queue": Q_STT, "provider": s.stt_provider}})

    while True:
        msg = read_task(stream=Q_STT, group=GROUP_STT, consumer=consumer, block_ms=5000)
        if not msg:
            continue

        should_ack = False
        try:
            with track_stage_latency("worker-stt", "stt"):
                task = msg.payload
                meeting_id = task["meeting_id"]
                chunk_seq = int(task.get("chunk_seq", 0))
                blob_key = task.get("blob_key") or None

                audio = get_bytes(blob_key)

                # sample_rate из задачи может отсутствовать, для whisper мы всё равно ресемплим в 16k
                res = stt.transcribe_chunk(audio=audio, sample_rate=16000)

                with db_session() as session:
                    mrepo = MeetingRepository(session)
                    srepo = TranscriptSegmentRepository(session)

                    # гарантируем Meeting (иначе FK упадёт) + ставим статус processing
                    m = mrepo.ensure(
                        meeting_id=meeting_id, meeting_context={"source": "auto_worker_stt"}
                    )
                    m.status = PipelineStatus.processing
                    mrepo.save(m)
                    seg = TranscriptSegment(
                        meeting_id=meeting_id,
                        seq=chunk_seq,
                        speaker=res.speaker,
                        start_ms=None,
                        end_ms=None,
                        raw_text=res.text or "",
                        enhanced_text=res.text or "",
                        confidence=res.confidence,
                    )
                    srepo.upsert_by_meeting_seq(seg)

                _publish_update(
                    meeting_id,
                    {
                        "schema_version": "v1",
                        "event_type": "transcript.update",
                        "meeting_id": meeting_id,
                        "seq": chunk_seq,
                        "speaker": res.speaker,
                        "raw_text": res.text or "",
                        "enhanced_text": res.text or "",
                        "confidence": res.confidence,
                    },
                )

                enqueue_enhancer(meeting_id=meeting_id)
            should_ack = True
            QUEUE_TASKS_TOTAL.labels(service="worker-stt", queue=Q_STT, result="success").inc()

        except Exception as e:
            log.error(
                "worker_stt_error",
                extra={
                    "payload": {"err": str(e)[:250], "task": task if "task" in locals() else None}
                },
            )
            QUEUE_TASKS_TOTAL.labels(service="worker-stt", queue=Q_STT, result="error").inc()
            try:
                task = task if "task" in locals() else {}
                requeue_with_backoff(
                    queue_name=Q_STT, task_payload=task, max_attempts=3, backoff_sec=1
                )
                should_ack = True
                QUEUE_TASKS_TOTAL.labels(service="worker-stt", queue=Q_STT, result="retry").inc()
            except Exception:
                pass
        finally:
            if should_ack:
                with suppress(Exception):
                    ack_task(stream=Q_STT, group=GROUP_STT, entry_id=msg.entry_id)


def main() -> None:
    setup_logging()
    while True:
        try:
            run_loop()
        except Exception as e:
            log.error("worker_stt_fatal", extra={"payload": {"err": str(e)[:250]}})
            time.sleep(2)


if __name__ == "__main__":
    main()
