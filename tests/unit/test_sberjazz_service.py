from __future__ import annotations

from contextlib import suppress

import pytest

from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.common.errors import ErrCode, ProviderError
from interview_analytics_agent.services import sberjazz_service


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}

    def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool | None = None,
    ) -> bool | None:
        _ = ex
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def delete(self, key: str) -> int:
        if key in self._store:
            del self._store[key]
            return 1
        return 0

    def sadd(self, key: str, value: str) -> int:
        self._sets.setdefault(key, set()).add(value)
        return 1

    def smembers(self, key: str) -> set[str]:
        return self._sets.get(key, set())

    def incr(self, key: str) -> int:
        cur = int(self._store.get(key, "0"))
        nxt = cur + 1
        self._store[key] = str(nxt)
        return nxt


class _FakeConnector:
    def __init__(self) -> None:
        self.join_calls = 0
        self.leave_calls = 0

    def join(self, meeting_id: str):
        self.join_calls += 1
        return {"meeting_id": meeting_id}

    def leave(self, meeting_id: str) -> None:
        _ = meeting_id
        self.leave_calls += 1

    def fetch_recording(self, meeting_id: str):
        _ = meeting_id
        return None

    def fetch_live_chunks(
        self, meeting_id: str, *, cursor: str | None = None, limit: int = 20
    ) -> dict | None:
        _ = meeting_id, cursor, limit
        return {"chunks": [], "next_cursor": cursor}


def test_join_state_persisted_and_readable_from_redis(monkeypatch) -> None:
    fake_redis = _FakeRedis()
    fake_connector = _FakeConnector()
    monkeypatch.setattr(sberjazz_service, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(
        sberjazz_service,
        "_resolve_connector",
        lambda: ("sberjazz_mock", fake_connector),
    )
    sberjazz_service._SESSIONS.clear()
    sberjazz_service._CIRCUIT_BREAKER = None

    joined = sberjazz_service.join_sberjazz_meeting("meeting-1")
    assert joined.connected is True
    assert fake_connector.join_calls == 1

    # Эмулируем новый процесс: удаляем in-memory state, читаем из Redis.
    sberjazz_service._SESSIONS.clear()
    loaded = sberjazz_service.get_sberjazz_meeting_state("meeting-1")
    assert loaded.connected is True
    assert loaded.meeting_id == "meeting-1"


def test_reconnect_calls_leave_then_join_when_connected(monkeypatch) -> None:
    fake_redis = _FakeRedis()
    fake_connector = _FakeConnector()
    monkeypatch.setattr(sberjazz_service, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(
        sberjazz_service,
        "_resolve_connector",
        lambda: ("sberjazz_mock", fake_connector),
    )
    sberjazz_service._SESSIONS.clear()
    sberjazz_service._CIRCUIT_BREAKER = None

    sberjazz_service.join_sberjazz_meeting("meeting-2")
    reconnected = sberjazz_service.reconnect_sberjazz_meeting("meeting-2")

    assert reconnected.connected is True
    assert fake_connector.leave_calls == 1
    assert fake_connector.join_calls >= 2


def test_reconcile_reconnects_stale_sessions(monkeypatch) -> None:
    fake_redis = _FakeRedis()
    monkeypatch.setattr(sberjazz_service, "redis_client", lambda: fake_redis)

    sberjazz_service._SESSIONS.clear()
    sberjazz_service._CIRCUIT_BREAKER = None
    sberjazz_service._SESSIONS["stale-1"] = sberjazz_service.SberJazzSessionState(
        meeting_id="stale-1",
        provider="sberjazz_mock",
        connected=True,
        attempts=1,
        last_error=None,
        updated_at="2020-01-01T00:00:00+00:00",
    )
    sberjazz_service._SESSIONS["fresh-1"] = sberjazz_service.SberJazzSessionState(
        meeting_id="fresh-1",
        provider="sberjazz_mock",
        connected=True,
        attempts=1,
        last_error=None,
        updated_at="2099-01-01T00:00:00+00:00",
    )

    called: list[str] = []

    def _fake_reconnect(meeting_id: str):
        called.append(meeting_id)
        return sberjazz_service.SberJazzSessionState(
            meeting_id=meeting_id,
            provider="sberjazz_mock",
            connected=True,
            attempts=2,
            last_error=None,
            updated_at="2099-01-01T00:00:01+00:00",
        )

    monkeypatch.setattr(sberjazz_service, "reconnect_sberjazz_meeting", _fake_reconnect)

    result = sberjazz_service.reconcile_sberjazz_sessions(limit=10)
    assert result.scanned >= 2
    assert result.stale >= 1
    assert result.reconnected >= 1
    assert "stale-1" in called


def test_circuit_breaker_opens_and_blocks_calls(monkeypatch) -> None:
    class _FailingConnector(_FakeConnector):
        def join(self, meeting_id: str):
            self.join_calls += 1
            raise RuntimeError(f"provider_down:{meeting_id}")

    fake_redis = _FakeRedis()
    failing_connector = _FailingConnector()
    monkeypatch.setattr(sberjazz_service, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(
        sberjazz_service,
        "_resolve_connector",
        lambda: ("sberjazz", failing_connector),
    )
    sberjazz_service._SESSIONS.clear()
    sberjazz_service._CIRCUIT_BREAKER = None

    settings = get_settings()
    prev_retries = settings.sberjazz_retries
    prev_threshold = settings.sberjazz_cb_failure_threshold
    prev_open_sec = settings.sberjazz_cb_open_sec
    settings.sberjazz_retries = 0
    settings.sberjazz_cb_failure_threshold = 2
    settings.sberjazz_cb_open_sec = 600

    try:
        for _ in range(2):
            with suppress(ProviderError):
                sberjazz_service.join_sberjazz_meeting("cb-1")

        state = sberjazz_service.get_sberjazz_circuit_breaker_state()
        assert state.state == "open"
        assert state.consecutive_failures >= 2

        with_provider_calls = failing_connector.join_calls
        try:
            sberjazz_service.join_sberjazz_meeting("cb-1")
        except ProviderError as e:
            assert "circuit breaker is open" in e.message
        assert failing_connector.join_calls == with_provider_calls
    finally:
        settings.sberjazz_retries = prev_retries
        settings.sberjazz_cb_failure_threshold = prev_threshold
        settings.sberjazz_cb_open_sec = prev_open_sec


def test_join_does_not_retry_on_non_retryable_provider_error(monkeypatch) -> None:
    class _AuthFailConnector(_FakeConnector):
        def join(self, meeting_id: str):
            self.join_calls += 1
            raise ProviderError(
                ErrCode.CONNECTOR_AUTH_ERROR,
                f"bad token for {meeting_id}",
            )

    fake_redis = _FakeRedis()
    connector = _AuthFailConnector()
    monkeypatch.setattr(sberjazz_service, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(
        sberjazz_service,
        "_resolve_connector",
        lambda: ("sberjazz", connector),
    )
    sberjazz_service._SESSIONS.clear()
    sberjazz_service._CIRCUIT_BREAKER = None

    settings = get_settings()
    prev_retries = settings.sberjazz_retries
    settings.sberjazz_retries = 5
    try:
        with pytest.raises(ProviderError) as e:
            sberjazz_service.join_sberjazz_meeting("auth-fail-1")
        assert e.value.code == ErrCode.CONNECTOR_AUTH_ERROR
        assert connector.join_calls == 1
    finally:
        settings.sberjazz_retries = prev_retries


def test_join_rejected_when_meeting_lock_is_busy(monkeypatch) -> None:
    fake_redis = _FakeRedis()
    fake_connector = _FakeConnector()
    monkeypatch.setattr(sberjazz_service, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(
        sberjazz_service,
        "_resolve_connector",
        lambda: ("sberjazz_mock", fake_connector),
    )
    sberjazz_service._SESSIONS.clear()
    sberjazz_service._CIRCUIT_BREAKER = None

    lock_key = sberjazz_service._op_lock_key("meeting-lock")
    fake_redis.set(lock_key, "already-locked", ex=60, nx=True)

    with pytest.raises(ProviderError) as e:
        sberjazz_service.join_sberjazz_meeting("meeting-lock")
    assert "Операция коннектора уже выполняется" in e.value.message


def test_pull_live_chunks_ingests_and_saves_cursor(monkeypatch) -> None:
    fake_redis = _FakeRedis()
    fake_connector = _FakeConnector()
    monkeypatch.setattr(sberjazz_service, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(
        sberjazz_service,
        "_resolve_connector",
        lambda: ("sberjazz_mock", fake_connector),
    )
    sberjazz_service._SESSIONS.clear()
    sberjazz_service._CIRCUIT_BREAKER = None
    sberjazz_service._SESSIONS["m-live-1"] = sberjazz_service.SberJazzSessionState(
        meeting_id="m-live-1",
        provider="sberjazz_mock",
        connected=True,
        attempts=1,
        last_error=None,
        updated_at="2020-01-01T00:00:00+00:00",
    )

    def _fetch_live_chunks(meeting_id: str, *, cursor: str | None = None, limit: int = 20):
        _ = meeting_id, cursor, limit
        return {
            "chunks": [{"id": "ch-1", "seq": 7, "content_b64": "YQ=="}],  # a
            "next_cursor": "cursor-2",
        }

    monkeypatch.setattr(fake_connector, "fetch_live_chunks", _fetch_live_chunks)
    calls: list[tuple[str, int, str]] = []
    monkeypatch.setattr(
        sberjazz_service,
        "ingest_audio_chunk_b64",
        lambda **kwargs: (
            calls.append((kwargs["meeting_id"], kwargs["seq"], kwargs["idempotency_key"]))
            or type("ChunkIngestResult", (), {"is_duplicate": False})()
        ),
    )

    result = sberjazz_service.pull_sberjazz_live_chunks(limit_sessions=10, batch_limit=10)
    assert result.connected == 1
    assert result.pulled == 1
    assert result.ingested == 1
    assert calls == [("m-live-1", 7, "ch-1")]
    assert fake_redis.get(sberjazz_service._live_cursor_key("m-live-1")) == "cursor-2"


def test_pull_live_chunks_marks_failed_on_invalid_payload(monkeypatch) -> None:
    fake_redis = _FakeRedis()
    fake_connector = _FakeConnector()
    monkeypatch.setattr(sberjazz_service, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(
        sberjazz_service,
        "_resolve_connector",
        lambda: ("sberjazz_mock", fake_connector),
    )
    sberjazz_service._SESSIONS.clear()
    sberjazz_service._CIRCUIT_BREAKER = None
    sberjazz_service._SESSIONS["m-live-2"] = sberjazz_service.SberJazzSessionState(
        meeting_id="m-live-2",
        provider="sberjazz_mock",
        connected=True,
        attempts=1,
        last_error=None,
        updated_at="2020-01-01T00:00:00+00:00",
    )

    monkeypatch.setattr(
        fake_connector,
        "fetch_live_chunks",
        lambda meeting_id, cursor=None, limit=20: {"chunks": "bad"},
    )

    result = sberjazz_service.pull_sberjazz_live_chunks(limit_sessions=10, batch_limit=10)
    assert result.connected == 1
    assert result.failed == 1
    assert result.pulled == 0
    assert result.ingested == 0


def test_pull_live_chunks_retries_fetch(monkeypatch) -> None:
    fake_redis = _FakeRedis()
    fake_connector = _FakeConnector()
    monkeypatch.setattr(sberjazz_service, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(
        sberjazz_service,
        "_resolve_connector",
        lambda: ("sberjazz_mock", fake_connector),
    )
    s = get_settings()
    snapshot_retries = s.sberjazz_live_pull_retries
    snapshot_backoff = s.sberjazz_live_pull_retry_backoff_ms
    try:
        s.sberjazz_live_pull_retries = 1
        s.sberjazz_live_pull_retry_backoff_ms = 0
        sberjazz_service._SESSIONS.clear()
        sberjazz_service._CIRCUIT_BREAKER = None
        sberjazz_service._SESSIONS["m-live-3"] = sberjazz_service.SberJazzSessionState(
            meeting_id="m-live-3",
            provider="sberjazz_mock",
            connected=True,
            attempts=1,
            last_error=None,
            updated_at="2020-01-01T00:00:00+00:00",
        )

        calls = {"n": 0}

        def _fetch_live_chunks(meeting_id: str, *, cursor: str | None = None, limit: int = 20):
            _ = meeting_id, cursor, limit
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("temporary")
            return {
                "chunks": [{"id": "ok-1", "seq": 2, "content_b64": "YQ=="}],
                "next_cursor": "c1",
            }

        monkeypatch.setattr(fake_connector, "fetch_live_chunks", _fetch_live_chunks)
        monkeypatch.setattr(
            sberjazz_service,
            "ingest_audio_chunk_b64",
            lambda **kwargs: type("ChunkIngestResult", (), {"is_duplicate": False})(),
        )

        result = sberjazz_service.pull_sberjazz_live_chunks(limit_sessions=10, batch_limit=10)
        assert calls["n"] == 2
        assert result.pulled == 1
        assert result.ingested == 1
    finally:
        s.sberjazz_live_pull_retries = snapshot_retries
        s.sberjazz_live_pull_retry_backoff_ms = snapshot_backoff


def test_pull_live_chunks_triggers_reconnect_after_threshold(monkeypatch) -> None:
    fake_redis = _FakeRedis()
    fake_connector = _FakeConnector()
    monkeypatch.setattr(sberjazz_service, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(
        sberjazz_service,
        "_resolve_connector",
        lambda: ("sberjazz_mock", fake_connector),
    )

    s = get_settings()
    snapshot_threshold = s.sberjazz_live_pull_fail_reconnect_threshold
    try:
        s.sberjazz_live_pull_fail_reconnect_threshold = 2
        sberjazz_service._SESSIONS.clear()
        sberjazz_service._CIRCUIT_BREAKER = None
        sberjazz_service._SESSIONS["m-live-th"] = sberjazz_service.SberJazzSessionState(
            meeting_id="m-live-th",
            provider="sberjazz_mock",
            connected=True,
            attempts=1,
            last_error=None,
            updated_at="2020-01-01T00:00:00+00:00",
        )

        monkeypatch.setattr(
            fake_connector,
            "fetch_live_chunks",
            lambda meeting_id, cursor=None, limit=20: (_ for _ in ()).throw(RuntimeError("down")),
        )

        reconnect_calls: list[str] = []
        monkeypatch.setattr(
            sberjazz_service,
            "reconnect_sberjazz_meeting",
            lambda meeting_id: (reconnect_calls.append(meeting_id) or True),
        )

        # 1-й сбой: ниже порога, reconnect не должен сработать.
        result_1 = sberjazz_service.pull_sberjazz_live_chunks(limit_sessions=10, batch_limit=10)
        assert result_1.failed == 1
        assert reconnect_calls == []

        # 2-й сбой: достигнут порог, reconnect должен сработать.
        result_2 = sberjazz_service.pull_sberjazz_live_chunks(limit_sessions=10, batch_limit=10)
        assert result_2.failed == 1
        assert reconnect_calls == ["m-live-th"]

        # Счетчик сбрасывается после попытки reconnect: следующий сбой снова "первый".
        result_3 = sberjazz_service.pull_sberjazz_live_chunks(limit_sessions=10, batch_limit=10)
        assert result_3.failed == 1
        assert reconnect_calls == ["m-live-th"]
    finally:
        s.sberjazz_live_pull_fail_reconnect_threshold = snapshot_threshold
