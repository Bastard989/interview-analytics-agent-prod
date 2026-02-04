from __future__ import annotations

from contextlib import suppress

import pytest

from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.common.errors import ProviderError
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
