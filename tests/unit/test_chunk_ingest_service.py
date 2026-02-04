from __future__ import annotations

from interview_analytics_agent.services.chunk_ingest_service import (
    ingest_audio_chunk_b64,
    ingest_audio_chunk_bytes,
)


def test_ingest_audio_chunk_bytes_enqueues(monkeypatch) -> None:
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        "interview_analytics_agent.services.chunk_ingest_service.check_and_set",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "interview_analytics_agent.services.chunk_ingest_service.put_bytes",
        lambda key, data: calls.update({"blob_key": key, "audio": data}),
    )
    monkeypatch.setattr(
        "interview_analytics_agent.services.chunk_ingest_service.enqueue_stt",
        lambda **kwargs: calls.update({"task": kwargs}),
    )

    result = ingest_audio_chunk_bytes(
        meeting_id="m-1",
        seq=3,
        audio_bytes=b"abc",
        idempotency_key="idem-1",
        idempotency_scope="audio_chunk_test",
    )
    assert result.accepted is True
    assert result.is_duplicate is False
    assert result.blob_key == "meetings/m-1/chunks/3.bin"
    assert calls["blob_key"] == "meetings/m-1/chunks/3.bin"
    assert calls["audio"] == b"abc"
    assert calls["task"] == {
        "meeting_id": "m-1",
        "chunk_seq": 3,
        "blob_key": "meetings/m-1/chunks/3.bin",
    }


def test_ingest_audio_chunk_bytes_duplicate(monkeypatch) -> None:
    monkeypatch.setattr(
        "interview_analytics_agent.services.chunk_ingest_service.check_and_set",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        "interview_analytics_agent.services.chunk_ingest_service.put_bytes",
        lambda key, data: (_ for _ in ()).throw(RuntimeError("must not write")),
    )
    monkeypatch.setattr(
        "interview_analytics_agent.services.chunk_ingest_service.enqueue_stt",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("must not enqueue")),
    )

    result = ingest_audio_chunk_bytes(
        meeting_id="m-2",
        seq=5,
        audio_bytes=b"dup",
        idempotency_key="idem-dup",
        idempotency_scope="audio_chunk_test",
    )
    assert result.accepted is True
    assert result.is_duplicate is True


def test_ingest_audio_chunk_b64_decodes_and_calls_bytes(monkeypatch) -> None:
    monkeypatch.setattr(
        "interview_analytics_agent.services.chunk_ingest_service.check_and_set",
        lambda *args, **kwargs: True,
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "interview_analytics_agent.services.chunk_ingest_service.put_bytes",
        lambda key, data: captured.update({"blob_key": key, "audio": data}),
    )
    monkeypatch.setattr(
        "interview_analytics_agent.services.chunk_ingest_service.enqueue_stt",
        lambda **kwargs: None,
    )

    result = ingest_audio_chunk_b64(
        meeting_id="m-3",
        seq=1,
        content_b64="YWFh",  # aaa
        idempotency_key="idem-b64",
        idempotency_scope="audio_chunk_test",
    )
    assert result.is_duplicate is False
    assert captured["audio"] == b"aaa"
