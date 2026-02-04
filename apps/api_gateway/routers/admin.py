"""
Service-only admin endpoints.

Назначение:
- безопасные внутренние операции для эксплуатации
- доступ только по service API key
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from apps.api_gateway.deps import service_auth_read_dep, service_auth_write_dep
from interview_analytics_agent.common.errors import ErrCode, ProviderError
from interview_analytics_agent.common.metrics import record_sberjazz_reconcile_result
from interview_analytics_agent.queue.redis import redis_client
from interview_analytics_agent.queue.streams import stream_dlq_name
from interview_analytics_agent.services.sberjazz_service import (
    SberJazzConnectorHealth,
    SberJazzReconcileResult,
    SberJazzSessionState,
    get_sberjazz_connector_health,
    get_sberjazz_meeting_state,
    join_sberjazz_meeting,
    leave_sberjazz_meeting,
    list_sberjazz_sessions,
    reconcile_sberjazz_sessions,
    reconnect_sberjazz_meeting,
)
from interview_analytics_agent.services.security_audit_service import list_security_audit_events

router = APIRouter()

_QUEUE_GROUPS = {
    "q:stt": "g:stt",
    "q:enhancer": "g:enhancer",
    "q:analytics": "g:analytics",
    "q:delivery": "g:delivery",
    "q:retention": "g:retention",
}


class QueueHealthItem(BaseModel):
    queue: str
    group: str
    depth: int
    pending: int
    dlq_depth: int


class QueueHealthResponse(BaseModel):
    queues: list[QueueHealthItem]


class SberJazzSessionResponse(BaseModel):
    meeting_id: str
    provider: str
    connected: bool
    attempts: int
    last_error: str | None
    updated_at: str


class SberJazzSessionListResponse(BaseModel):
    sessions: list[SberJazzSessionResponse]


class SberJazzConnectorHealthResponse(BaseModel):
    provider: str
    configured: bool
    healthy: bool
    details: dict[str, str] = Field(default_factory=dict)
    updated_at: str


class SberJazzReconcileResponse(BaseModel):
    scanned: int
    stale: int
    reconnected: int
    failed: int
    stale_threshold_sec: int
    updated_at: str


class SecurityAuditEventResponse(BaseModel):
    id: int
    created_at: str
    outcome: str
    endpoint: str
    method: str
    subject: str
    auth_type: str
    reason: str
    error_code: str | None
    status_code: int
    client_ip: str | None


class SecurityAuditListResponse(BaseModel):
    events: list[SecurityAuditEventResponse]


def _as_response(state: SberJazzSessionState) -> SberJazzSessionResponse:
    return SberJazzSessionResponse(
        meeting_id=state.meeting_id,
        provider=state.provider,
        connected=state.connected,
        attempts=state.attempts,
        last_error=state.last_error,
        updated_at=state.updated_at,
    )


def _as_health_response(state: SberJazzConnectorHealth) -> SberJazzConnectorHealthResponse:
    return SberJazzConnectorHealthResponse(
        provider=state.provider,
        configured=state.configured,
        healthy=state.healthy,
        details=state.details,
        updated_at=state.updated_at,
    )


def _as_reconcile_response(state: SberJazzReconcileResult) -> SberJazzReconcileResponse:
    return SberJazzReconcileResponse(
        scanned=state.scanned,
        stale=state.stale,
        reconnected=state.reconnected,
        failed=state.failed,
        stale_threshold_sec=state.stale_threshold_sec,
        updated_at=state.updated_at,
    )


def _xpending_count(r, stream: str, group: str) -> int:
    pending = r.xpending(stream, group)
    if isinstance(pending, dict):
        return int(pending.get("pending", 0))
    return 0


@router.get(
    "/admin/queues/health",
    response_model=QueueHealthResponse,
    dependencies=[Depends(service_auth_read_dep)],
)
def admin_queues_health() -> QueueHealthResponse:
    try:
        r = redis_client()
        queues = [
            QueueHealthItem(
                queue=queue,
                group=group,
                depth=int(r.xlen(queue)),
                pending=_xpending_count(r, queue, group),
                dlq_depth=int(r.xlen(stream_dlq_name(queue))),
            )
            for queue, group in _QUEUE_GROUPS.items()
        ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": ErrCode.REDIS_ERROR,
                "message": "Не удалось получить состояние очередей",
                "details": {"err": str(e)[:200]},
            },
        ) from e

    return QueueHealthResponse(queues=queues)


@router.post(
    "/admin/connectors/sberjazz/{meeting_id}/join",
    response_model=SberJazzSessionResponse,
    dependencies=[Depends(service_auth_write_dep)],
)
def admin_sberjazz_join(meeting_id: str) -> SberJazzSessionResponse:
    try:
        state = join_sberjazz_meeting(meeting_id)
    except ProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": e.code, "message": e.message, "details": e.details or {}},
        ) from e
    return _as_response(state)


@router.post(
    "/admin/connectors/sberjazz/{meeting_id}/leave",
    response_model=SberJazzSessionResponse,
    dependencies=[Depends(service_auth_write_dep)],
)
def admin_sberjazz_leave(meeting_id: str) -> SberJazzSessionResponse:
    try:
        state = leave_sberjazz_meeting(meeting_id)
    except ProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": e.code, "message": e.message, "details": e.details or {}},
        ) from e
    return _as_response(state)


@router.get(
    "/admin/connectors/sberjazz/{meeting_id}/status",
    response_model=SberJazzSessionResponse,
    dependencies=[Depends(service_auth_read_dep)],
)
def admin_sberjazz_status(meeting_id: str) -> SberJazzSessionResponse:
    return _as_response(get_sberjazz_meeting_state(meeting_id))


@router.post(
    "/admin/connectors/sberjazz/{meeting_id}/reconnect",
    response_model=SberJazzSessionResponse,
    dependencies=[Depends(service_auth_write_dep)],
)
def admin_sberjazz_reconnect(meeting_id: str) -> SberJazzSessionResponse:
    try:
        state = reconnect_sberjazz_meeting(meeting_id)
    except ProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": e.code, "message": e.message, "details": e.details or {}},
        ) from e
    return _as_response(state)


@router.get(
    "/admin/connectors/sberjazz/health",
    response_model=SberJazzConnectorHealthResponse,
    dependencies=[Depends(service_auth_read_dep)],
)
def admin_sberjazz_health() -> SberJazzConnectorHealthResponse:
    return _as_health_response(get_sberjazz_connector_health())


@router.get(
    "/admin/connectors/sberjazz/sessions",
    response_model=SberJazzSessionListResponse,
    dependencies=[Depends(service_auth_read_dep)],
)
def admin_sberjazz_sessions(limit: int = 100) -> SberJazzSessionListResponse:
    sessions = [_as_response(s) for s in list_sberjazz_sessions(limit=max(1, min(limit, 500)))]
    return SberJazzSessionListResponse(sessions=sessions)


@router.post(
    "/admin/connectors/sberjazz/reconcile",
    response_model=SberJazzReconcileResponse,
    dependencies=[Depends(service_auth_write_dep)],
)
def admin_sberjazz_reconcile(limit: int = 200) -> SberJazzReconcileResponse:
    result = reconcile_sberjazz_sessions(limit=max(1, min(limit, 500)))
    record_sberjazz_reconcile_result(
        source="admin",
        stale=result.stale,
        failed=result.failed,
        reconnected=result.reconnected,
    )
    return _as_reconcile_response(result)


@router.get(
    "/admin/security/audit",
    response_model=SecurityAuditListResponse,
    dependencies=[Depends(service_auth_read_dep)],
)
def admin_security_audit(
    limit: int = 100,
    outcome: str | None = None,
    subject: str | None = None,
) -> SecurityAuditListResponse:
    try:
        events = list_security_audit_events(limit=limit, outcome=outcome, subject=subject)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": ErrCode.VALIDATION, "message": str(e)},
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": ErrCode.DB_ERROR,
                "message": "Не удалось загрузить security audit события",
                "details": {"err": str(e)[:200]},
            },
        ) from e
    return SecurityAuditListResponse(events=[SecurityAuditEventResponse(**event.__dict__) for event in events])
