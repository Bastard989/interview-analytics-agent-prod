"""
Service layer для SberJazz connector.

Содержит:
- выбор провайдера коннектора (real/mock)
- retry/backoff для join/leave
- хранение состояния сессии (in-memory + Redis)
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import uuid4

from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.common.errors import ErrCode, ProviderError
from interview_analytics_agent.common.logging import get_project_logger
from interview_analytics_agent.connectors.base import MeetingConnector
from interview_analytics_agent.connectors.salutejazz.adapter import SaluteJazzConnector
from interview_analytics_agent.connectors.salutejazz.mock import MockSaluteJazzConnector
from interview_analytics_agent.queue.redis import redis_client
from interview_analytics_agent.services.chunk_ingest_service import ingest_audio_chunk_b64

log = get_project_logger()


@dataclass
class SberJazzSessionState:
    meeting_id: str
    provider: str
    connected: bool
    attempts: int
    last_error: str | None
    updated_at: str


_SESSIONS: dict[str, SberJazzSessionState] = {}
_SESSION_KEY_PREFIX = "connector:sberjazz:session:"
_SESSION_INDEX_KEY = "connector:sberjazz:sessions"
_CIRCUIT_BREAKER_KEY = "connector:sberjazz:circuit_breaker"
_OP_LOCK_KEY_PREFIX = "connector:sberjazz:op_lock:"
_LIVE_CURSOR_KEY_PREFIX = "connector:sberjazz:live_cursor:"
_LIVE_SEQ_KEY_PREFIX = "connector:sberjazz:live_seq:"


@dataclass
class SberJazzCircuitBreakerState:
    state: str  # closed|open|half_open
    consecutive_failures: int
    opened_at: str | None
    last_error: str | None
    updated_at: str


_CIRCUIT_BREAKER: SberJazzCircuitBreakerState | None = None


@dataclass
class SberJazzLivePullResult:
    scanned: int
    connected: int
    pulled: int
    ingested: int
    failed: int
    updated_at: str


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _resolve_connector() -> tuple[str, MeetingConnector]:
    s = get_settings()
    provider = (s.meeting_connector_provider or "sberjazz_mock").strip().lower()
    if provider == "sberjazz":
        return provider, SaluteJazzConnector()
    if provider == "sberjazz_mock":
        return provider, MockSaluteJazzConnector()
    raise ProviderError(
        ErrCode.CONNECTOR_PROVIDER_ERROR,
        f"Неизвестный provider: {provider}",
        details={"allowed": "sberjazz,sberjazz_mock"},
    )


def _retry_config() -> tuple[int, float]:
    s = get_settings()
    attempts = max(1, int(s.sberjazz_retries) + 1)
    backoff_sec = max(0, int(s.sberjazz_retry_backoff_ms)) / 1000.0
    return attempts, backoff_sec


def _session_ttl_sec() -> int:
    s = get_settings()
    return max(60, int(getattr(s, "sberjazz_session_ttl_sec", 86_400)))


def _session_key(meeting_id: str) -> str:
    return f"{_SESSION_KEY_PREFIX}{meeting_id}"


def _live_cursor_key(meeting_id: str) -> str:
    return f"{_LIVE_CURSOR_KEY_PREFIX}{meeting_id}"


def _live_seq_key(meeting_id: str) -> str:
    return f"{_LIVE_SEQ_KEY_PREFIX}{meeting_id}"


def _op_lock_key(meeting_id: str) -> str:
    return f"{_OP_LOCK_KEY_PREFIX}{meeting_id}"


def _op_lock_ttl_sec() -> int:
    return max(10, int(getattr(get_settings(), "sberjazz_op_lock_ttl_sec", 60)))


def _acquire_op_lock(*, meeting_id: str, token: str) -> bool:
    return bool(redis_client().set(_op_lock_key(meeting_id), token, nx=True, ex=_op_lock_ttl_sec()))


def _release_op_lock(*, meeting_id: str, token: str) -> None:
    key = _op_lock_key(meeting_id)
    r = redis_client()
    current = r.get(key)
    if current == token:
        r.delete(key)


@contextmanager
def _meeting_operation_lock(*, meeting_id: str, operation: str):
    token = uuid4().hex
    if not _acquire_op_lock(meeting_id=meeting_id, token=token):
        raise ProviderError(
            ErrCode.CONNECTOR_PROVIDER_ERROR,
            "Операция коннектора уже выполняется для meeting",
            details={"meeting_id": meeting_id, "operation": operation},
        )
    try:
        yield
    finally:
        try:
            _release_op_lock(meeting_id=meeting_id, token=token)
        except Exception as e:
            log.warning(
                "sberjazz_op_lock_release_failed",
                extra={
                    "payload": {
                        "meeting_id": meeting_id,
                        "operation": operation,
                        "error": str(e)[:200],
                    }
                },
            )


def _save_state_redis(state: SberJazzSessionState) -> None:
    r = redis_client()
    payload = json.dumps(asdict(state), ensure_ascii=False)
    r.set(_session_key(state.meeting_id), payload, ex=_session_ttl_sec())
    r.sadd(_SESSION_INDEX_KEY, state.meeting_id)


def _load_state_redis(meeting_id: str) -> SberJazzSessionState | None:
    raw = redis_client().get(_session_key(meeting_id))
    if not raw:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):
        return None
    try:
        return SberJazzSessionState(
            meeting_id=str(data["meeting_id"]),
            provider=str(data["provider"]),
            connected=bool(data["connected"]),
            attempts=int(data["attempts"]),
            last_error=str(data["last_error"]) if data.get("last_error") is not None else None,
            updated_at=str(data["updated_at"]),
        )
    except Exception:
        return None


def _save_state(state: SberJazzSessionState) -> SberJazzSessionState:
    _SESSIONS[state.meeting_id] = state
    try:
        _save_state_redis(state)
    except Exception as e:
        log.warning(
            "sberjazz_state_redis_write_failed",
            extra={"payload": {"meeting_id": state.meeting_id, "error": str(e)[:200]}},
        )
    return state


def _load_live_cursor(meeting_id: str) -> str | None:
    raw = redis_client().get(_live_cursor_key(meeting_id))
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def _save_live_cursor(meeting_id: str, cursor: str) -> None:
    redis_client().set(_live_cursor_key(meeting_id), cursor, ex=_session_ttl_sec())


def _next_live_seq(meeting_id: str) -> int:
    return int(redis_client().incr(_live_seq_key(meeting_id)))


def _touch_connected_state(meeting_id: str) -> None:
    prev = get_sberjazz_meeting_state(meeting_id)
    state = SberJazzSessionState(
        meeting_id=prev.meeting_id,
        provider=prev.provider,
        connected=True,
        attempts=prev.attempts,
        last_error=prev.last_error,
        updated_at=_now_iso(),
    )
    _save_state(state)


def get_sberjazz_meeting_state(meeting_id: str) -> SberJazzSessionState:
    try:
        state = _load_state_redis(meeting_id)
        if state:
            _SESSIONS[meeting_id] = state
            return state
    except Exception as e:
        log.warning(
            "sberjazz_state_redis_read_failed",
            extra={"payload": {"meeting_id": meeting_id, "error": str(e)[:200]}},
        )

    state = _SESSIONS.get(meeting_id)
    if state:
        return state
    provider, _ = _resolve_connector()
    return SberJazzSessionState(
        meeting_id=meeting_id,
        provider=provider,
        connected=False,
        attempts=0,
        last_error=None,
        updated_at=_now_iso(),
    )


def _parse_dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)


def _cb_failure_threshold() -> int:
    return max(1, int(getattr(get_settings(), "sberjazz_cb_failure_threshold", 5)))


def _cb_open_sec() -> int:
    return max(5, int(getattr(get_settings(), "sberjazz_cb_open_sec", 60)))


def _default_cb_state() -> SberJazzCircuitBreakerState:
    return SberJazzCircuitBreakerState(
        state="closed",
        consecutive_failures=0,
        opened_at=None,
        last_error=None,
        updated_at=_now_iso(),
    )


def _save_cb_state_redis(state: SberJazzCircuitBreakerState) -> None:
    payload = json.dumps(asdict(state), ensure_ascii=False)
    redis_client().set(_CIRCUIT_BREAKER_KEY, payload, ex=_session_ttl_sec())


def _load_cb_state_redis() -> SberJazzCircuitBreakerState | None:
    raw = redis_client().get(_CIRCUIT_BREAKER_KEY)
    if not raw:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):
        return None
    try:
        return SberJazzCircuitBreakerState(
            state=str(data["state"]),
            consecutive_failures=int(data["consecutive_failures"]),
            opened_at=str(data["opened_at"]) if data.get("opened_at") else None,
            last_error=str(data["last_error"]) if data.get("last_error") else None,
            updated_at=str(data["updated_at"]),
        )
    except Exception:
        return None


def _save_cb_state(state: SberJazzCircuitBreakerState) -> SberJazzCircuitBreakerState:
    global _CIRCUIT_BREAKER

    _CIRCUIT_BREAKER = state
    try:
        _save_cb_state_redis(state)
    except Exception as e:
        log.warning(
            "sberjazz_cb_redis_write_failed",
            extra={"payload": {"error": str(e)[:200]}},
        )
    return state


def get_sberjazz_circuit_breaker_state() -> SberJazzCircuitBreakerState:
    global _CIRCUIT_BREAKER

    try:
        state = _load_cb_state_redis()
        if state:
            _CIRCUIT_BREAKER = state
            return state
    except Exception as e:
        log.warning(
            "sberjazz_cb_redis_read_failed",
            extra={"payload": {"error": str(e)[:200]}},
        )
    if _CIRCUIT_BREAKER:
        return _CIRCUIT_BREAKER
    return _default_cb_state()


def reset_sberjazz_circuit_breaker(*, reason: str = "manual_reset") -> SberJazzCircuitBreakerState:
    state = _default_cb_state()
    saved = _save_cb_state(state)
    log.info(
        "sberjazz_cb_reset",
        extra={
            "payload": {
                "reason": reason,
                "state": saved.state,
            }
        },
    )
    return saved


def _before_connector_call(operation: str) -> None:
    state = get_sberjazz_circuit_breaker_state()
    if state.state != "open":
        return

    opened_at = _parse_dt(state.opened_at or _now_iso())
    age_sec = max(0, int((datetime.now(UTC) - opened_at).total_seconds()))
    cooldown = _cb_open_sec()
    if age_sec < cooldown:
        raise ProviderError(
            ErrCode.CONNECTOR_PROVIDER_ERROR,
            "SberJazz connector circuit breaker is open",
            details={
                "operation": operation,
                "retry_after_sec": max(0, cooldown - age_sec),
                "consecutive_failures": state.consecutive_failures,
            },
        )

    _save_cb_state(
        SberJazzCircuitBreakerState(
            state="half_open",
            consecutive_failures=state.consecutive_failures,
            opened_at=state.opened_at,
            last_error=state.last_error,
            updated_at=_now_iso(),
        )
    )
    log.info(
        "sberjazz_cb_half_open",
        extra={"payload": {"operation": operation}},
    )


def _on_connector_success() -> None:
    state = get_sberjazz_circuit_breaker_state()
    if state.state == "closed" and state.consecutive_failures == 0:
        return
    _save_cb_state(_default_cb_state())
    log.info("sberjazz_cb_closed", extra={"payload": {"reason": "success"}})


def _on_connector_failure(*, operation: str, error: str | None) -> None:
    prev = get_sberjazz_circuit_breaker_state()
    failures = max(1, prev.consecutive_failures + 1)
    threshold = _cb_failure_threshold()

    should_open = failures >= threshold or prev.state == "half_open"
    next_state = SberJazzCircuitBreakerState(
        state="open" if should_open else "closed",
        consecutive_failures=failures,
        opened_at=_now_iso() if should_open else None,
        last_error=error,
        updated_at=_now_iso(),
    )
    _save_cb_state(next_state)
    log.warning(
        "sberjazz_cb_failure",
        extra={
            "payload": {
                "operation": operation,
                "failures": failures,
                "threshold": threshold,
                "state": next_state.state,
            }
        },
    )


def list_sberjazz_sessions(limit: int = 100) -> list[SberJazzSessionState]:
    meeting_ids: set[str] = set(_SESSIONS.keys())
    try:
        from_redis = redis_client().smembers(_SESSION_INDEX_KEY)
        meeting_ids.update(str(v) for v in from_redis if str(v).strip())
    except Exception as e:
        log.warning(
            "sberjazz_sessions_list_redis_failed",
            extra={"payload": {"error": str(e)[:200]}},
        )

    states = [get_sberjazz_meeting_state(mid) for mid in meeting_ids]
    states.sort(key=lambda x: _parse_dt(x.updated_at), reverse=True)
    return states[: max(1, limit)]


def _join_sberjazz_meeting_impl(meeting_id: str) -> SberJazzSessionState:
    _before_connector_call("join")
    provider, connector = _resolve_connector()
    attempts, backoff_sec = _retry_config()
    last_error: str | None = None

    for attempt in range(1, attempts + 1):
        try:
            connector.join(meeting_id)
            state = SberJazzSessionState(
                meeting_id=meeting_id,
                provider=provider,
                connected=True,
                attempts=attempt,
                last_error=None,
                updated_at=_now_iso(),
            )
            log.info(
                "sberjazz_join_success",
                extra={"payload": {"meeting_id": meeting_id, "attempt": attempt}},
            )
            _on_connector_success()
            return _save_state(state)
        except Exception as e:
            last_error = str(e)[:300]
            log.warning(
                "sberjazz_join_retry",
                extra={
                    "payload": {
                        "meeting_id": meeting_id,
                        "attempt": attempt,
                        "error": last_error,
                    }
                },
            )
            if attempt < attempts and backoff_sec > 0:
                time.sleep(backoff_sec * attempt)

    state = SberJazzSessionState(
        meeting_id=meeting_id,
        provider=provider,
        connected=False,
        attempts=attempts,
        last_error=last_error,
        updated_at=_now_iso(),
    )
    _save_state(state)
    _on_connector_failure(operation="join", error=last_error)
    raise ProviderError(
        ErrCode.CONNECTOR_PROVIDER_ERROR,
        "SberJazz join не выполнен после retries",
        details=asdict(state),
    )


def join_sberjazz_meeting(meeting_id: str) -> SberJazzSessionState:
    with _meeting_operation_lock(meeting_id=meeting_id, operation="join"):
        return _join_sberjazz_meeting_impl(meeting_id)


def _leave_sberjazz_meeting_impl(meeting_id: str) -> SberJazzSessionState:
    _before_connector_call("leave")
    provider, connector = _resolve_connector()
    attempts, backoff_sec = _retry_config()
    last_error: str | None = None

    for attempt in range(1, attempts + 1):
        try:
            connector.leave(meeting_id)
            state = SberJazzSessionState(
                meeting_id=meeting_id,
                provider=provider,
                connected=False,
                attempts=attempt,
                last_error=None,
                updated_at=_now_iso(),
            )
            log.info(
                "sberjazz_leave_success",
                extra={"payload": {"meeting_id": meeting_id, "attempt": attempt}},
            )
            _on_connector_success()
            return _save_state(state)
        except Exception as e:
            last_error = str(e)[:300]
            log.warning(
                "sberjazz_leave_retry",
                extra={
                    "payload": {
                        "meeting_id": meeting_id,
                        "attempt": attempt,
                        "error": last_error,
                    }
                },
            )
            if attempt < attempts and backoff_sec > 0:
                time.sleep(backoff_sec * attempt)

    state = SberJazzSessionState(
        meeting_id=meeting_id,
        provider=provider,
        connected=True,
        attempts=attempts,
        last_error=last_error,
        updated_at=_now_iso(),
    )
    _save_state(state)
    _on_connector_failure(operation="leave", error=last_error)
    raise ProviderError(
        ErrCode.CONNECTOR_PROVIDER_ERROR,
        "SberJazz leave не выполнен после retries",
        details=asdict(state),
    )


def leave_sberjazz_meeting(meeting_id: str) -> SberJazzSessionState:
    with _meeting_operation_lock(meeting_id=meeting_id, operation="leave"):
        return _leave_sberjazz_meeting_impl(meeting_id)


def reconnect_sberjazz_meeting(meeting_id: str) -> SberJazzSessionState:
    with _meeting_operation_lock(meeting_id=meeting_id, operation="reconnect"):
        state = get_sberjazz_meeting_state(meeting_id)
        if state.connected:
            try:
                _leave_sberjazz_meeting_impl(meeting_id)
            except ProviderError as e:
                log.warning(
                    "sberjazz_reconnect_leave_failed",
                    extra={"payload": {"meeting_id": meeting_id, "error": str(e)[:200]}},
                )
        return _join_sberjazz_meeting_impl(meeting_id)


@dataclass
class SberJazzConnectorHealth:
    provider: str
    configured: bool
    healthy: bool
    details: dict[str, str]
    updated_at: str


@dataclass
class SberJazzReconcileResult:
    scanned: int
    stale: int
    reconnected: int
    failed: int
    stale_threshold_sec: int
    updated_at: str


def reconcile_sberjazz_sessions(limit: int = 200) -> SberJazzReconcileResult:
    stale_threshold_sec = max(30, int(getattr(get_settings(), "sberjazz_reconcile_stale_sec", 900)))
    now = datetime.now(UTC)
    scanned = 0
    stale = 0
    reconnected = 0
    failed = 0

    for state in list_sberjazz_sessions(limit=limit):
        scanned += 1
        if not state.connected:
            continue
        age_sec = (now - _parse_dt(state.updated_at)).total_seconds()
        if age_sec < stale_threshold_sec:
            continue

        stale += 1
        try:
            reconnect_sberjazz_meeting(state.meeting_id)
            reconnected += 1
        except Exception as e:
            failed += 1
            log.warning(
                "sberjazz_reconcile_reconnect_failed",
                extra={
                    "payload": {
                        "meeting_id": state.meeting_id,
                        "error": str(e)[:300],
                    }
                },
            )

    return SberJazzReconcileResult(
        scanned=scanned,
        stale=stale,
        reconnected=reconnected,
        failed=failed,
        stale_threshold_sec=stale_threshold_sec,
        updated_at=_now_iso(),
    )


def _normalize_chunk_seq(meeting_id: str, raw_seq: object | None) -> int:
    if isinstance(raw_seq, int) and raw_seq >= 0:
        return raw_seq
    if isinstance(raw_seq, str) and raw_seq.isdigit():
        return int(raw_seq)
    return _next_live_seq(meeting_id)


def _pull_live_for_meeting(meeting_id: str, *, batch_limit: int) -> tuple[int, int]:
    provider_name = (get_settings().meeting_connector_provider or "").strip().lower()
    if provider_name in {"", "none"}:
        return 0, 0

    _provider, connector = _resolve_connector()

    fetch_fn = getattr(connector, "fetch_live_chunks", None)
    if not callable(fetch_fn):
        return 0, 0

    cursor = _load_live_cursor(meeting_id)
    payload = fetch_fn(meeting_id, cursor=cursor, limit=max(1, int(batch_limit))) or {}
    if not isinstance(payload, dict):
        return 0, 0

    chunks = payload.get("chunks")
    if not isinstance(chunks, list):
        chunks = []

    next_cursor_raw = payload.get("next_cursor")
    next_cursor = str(next_cursor_raw).strip() if next_cursor_raw is not None else None
    if next_cursor:
        _save_live_cursor(meeting_id, next_cursor)

    pulled = 0
    ingested = 0
    for idx, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            continue
        content_b64 = str(chunk.get("content_b64") or "").strip()
        if not content_b64:
            continue
        seq = _normalize_chunk_seq(meeting_id, chunk.get("seq"))
        chunk_id = str(
            chunk.get("id") or chunk.get("chunk_id") or f"{next_cursor or cursor or 'none'}:{idx}"
        )
        result = ingest_audio_chunk_b64(
            meeting_id=meeting_id,
            seq=seq,
            content_b64=content_b64,
            idempotency_key=chunk_id,
            idempotency_scope="audio_chunk_connector_live",
            idempotency_prefix="sj-live",
        )
        pulled += 1
        if not result.is_duplicate:
            ingested += 1

    if pulled > 0:
        _touch_connected_state(meeting_id)
    return pulled, ingested


def pull_sberjazz_live_chunks(
    *, limit_sessions: int = 100, batch_limit: int = 20
) -> SberJazzLivePullResult:
    sessions = list_sberjazz_sessions(limit=max(1, int(limit_sessions)))
    scanned = 0
    connected = 0
    pulled = 0
    ingested = 0
    failed = 0

    for state in sessions:
        scanned += 1
        if not state.connected:
            continue
        connected += 1
        try:
            m_pulled, m_ingested = _pull_live_for_meeting(
                state.meeting_id, batch_limit=max(1, int(batch_limit))
            )
            pulled += m_pulled
            ingested += m_ingested
        except Exception as e:
            failed += 1
            log.warning(
                "sberjazz_live_pull_failed",
                extra={"payload": {"meeting_id": state.meeting_id, "error": str(e)[:300]}},
            )

    return SberJazzLivePullResult(
        scanned=scanned,
        connected=connected,
        pulled=pulled,
        ingested=ingested,
        failed=failed,
        updated_at=_now_iso(),
    )


def get_sberjazz_connector_health() -> SberJazzConnectorHealth:
    provider, connector = _resolve_connector()
    cb = get_sberjazz_circuit_breaker_state()
    if provider == "sberjazz_mock":
        return SberJazzConnectorHealth(
            provider=provider,
            configured=True,
            healthy=True,
            details={"mode": "mock", "circuit_breaker": cb.state},
            updated_at=_now_iso(),
        )

    s = get_settings()
    configured = bool((s.sberjazz_api_base or "").strip())
    if not configured:
        return SberJazzConnectorHealth(
            provider=provider,
            configured=False,
            healthy=False,
            details={"error": "SBERJAZZ_API_BASE is empty", "circuit_breaker": cb.state},
            updated_at=_now_iso(),
        )

    try:
        # best-effort health ping for real connector
        health_fn = getattr(connector, "health", None)
        if callable(health_fn):
            health_fn()
        return SberJazzConnectorHealth(
            provider=provider,
            configured=True,
            healthy=True,
            details={"circuit_breaker": cb.state},
            updated_at=_now_iso(),
        )
    except Exception as e:
        return SberJazzConnectorHealth(
            provider=provider,
            configured=True,
            healthy=False,
            details={"error": str(e)[:300], "circuit_breaker": cb.state},
            updated_at=_now_iso(),
        )
