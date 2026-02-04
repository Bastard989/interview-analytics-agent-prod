"""
WebSocket обработчик.

Протокол (MVP):
- клиент присылает JSON {"event_type":"audio.chunk", ...}
- payload содержит base64 audio (content_b64), seq, meeting_id, sample_rate, channels, codec
- gateway сохраняет аудио в локальное хранилище и ставит задачу STT
- воркеры публикуют transcript.update в Redis pubsub channel ws:<meeting_id>
- gateway подписывается и ретранслирует клиенту

Важно:
- в MVP не делаем сложный backpressure, только базовая дедупликация
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.common.errors import ErrCode, UnauthorizedError
from interview_analytics_agent.common.logging import get_project_logger
from interview_analytics_agent.common.security import (
    AuthContext,
    has_any_service_permission,
    is_service_jwt_claims,
    require_auth,
)
from interview_analytics_agent.common.utils import b64_decode, safe_dict
from interview_analytics_agent.queue.redis import redis_client
from interview_analytics_agent.services.chunk_ingest_service import ingest_audio_chunk_bytes

log = get_project_logger()

ws_router = APIRouter()


def _is_service_ctx(ctx: AuthContext) -> bool:
    return ctx.auth_type == "service_api_key" or (
        ctx.auth_type == "jwt" and is_service_jwt_claims(ctx.claims)
    )


def _ws_client_ip(ws: WebSocket) -> str | None:
    return ws.client.host if ws.client else None


def _parse_scopes(raw: str) -> set[str]:
    return {s.strip() for s in (raw or "").split(",") if s.strip()}


def _audit_ws_allow(*, ws: WebSocket, ctx: AuthContext, reason: str) -> None:
    log.info(
        "security_audit_allow",
        extra={
            "payload": {
                "endpoint": ws.url.path,
                "method": "WS",
                "subject": ctx.subject,
                "auth_type": ctx.auth_type,
                "reason": reason,
                "client_ip": _ws_client_ip(ws),
            }
        },
    )


def _audit_ws_deny(
    *,
    ws: WebSocket,
    reason: str,
    error_code: str,
    auth_type: str = "unknown",
    subject: str = "unknown",
) -> None:
    log.warning(
        "security_audit_deny",
        extra={
            "payload": {
                "endpoint": ws.url.path,
                "method": "WS",
                "status_code": status.WS_1008_POLICY_VIOLATION,
                "reason": reason,
                "error_code": error_code,
                "auth_type": auth_type,
                "subject": subject,
                "client_ip": _ws_client_ip(ws),
            }
        },
    )


async def _forward_pubsub_to_ws(ws: WebSocket, meeting_id: str) -> None:
    """
    Фоновая задача: читает pubsub канал ws:<meeting_id> и шлёт сообщения в websocket.
    Реализация через asyncio.to_thread, потому что redis_client() синхронный.
    """
    channel = f"ws:{meeting_id}"
    r = redis_client()
    pubsub = r.pubsub()
    pubsub.subscribe(channel)

    try:
        while True:
            msg = await asyncio.to_thread(pubsub.get_message, True, 1.0)
            if not msg:
                await asyncio.sleep(0.01)
                continue
            if msg.get("type") != "message":
                continue

            data = msg.get("data")
            if not data:
                continue

            # data ожидаем как JSON-строку
            try:
                await ws.send_text(data)
            except Exception:
                break
    finally:
        try:
            pubsub.unsubscribe(channel)
            pubsub.close()
        except Exception:
            pass


async def _authorize_ws(ws: WebSocket, *, service_only: bool) -> AuthContext | None:
    try:
        ctx = require_auth(
            authorization=ws.headers.get("authorization"),
            x_api_key=ws.headers.get("x-api-key"),
        )
    except UnauthorizedError as e:
        _audit_ws_deny(ws=ws, reason=e.message, error_code=e.code)
        await ws.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason=f"{e.code}: {e.message}",
        )
        return None

    if service_only:
        if not _is_service_ctx(ctx):
            _audit_ws_deny(
                ws=ws,
                reason="not_service_identity",
                error_code=ErrCode.FORBIDDEN,
                auth_type=ctx.auth_type,
                subject=ctx.subject,
            )
            await ws.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="forbidden: service identity required",
            )
            return None
        if ctx.auth_type == "jwt":
            required_scopes = _parse_scopes(get_settings().jwt_service_required_scopes_ws_internal)
            if required_scopes and not has_any_service_permission(
                ctx.claims, required_permissions=required_scopes
            ):
                _audit_ws_deny(
                    ws=ws,
                    reason="missing_service_scope",
                    error_code=ErrCode.FORBIDDEN,
                    auth_type=ctx.auth_type,
                    subject=ctx.subject,
                )
                await ws.close(
                    code=status.WS_1008_POLICY_VIOLATION,
                    reason="forbidden: missing service scope",
                )
                return None
        _audit_ws_allow(ws=ws, ctx=ctx, reason="ws_service_auth_ok")
        return ctx

    # user websocket endpoint
    if _is_service_ctx(ctx):
        _audit_ws_deny(
            ws=ws,
            reason="service_identity_not_allowed",
            error_code=ErrCode.FORBIDDEN,
            auth_type=ctx.auth_type,
            subject=ctx.subject,
        )
        await ws.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="forbidden: use /v1/ws/internal for service identities",
        )
        return None

    _audit_ws_allow(ws=ws, ctx=ctx, reason="ws_user_auth_ok")
    return ctx


async def _websocket_endpoint_impl(ws: WebSocket, *, service_only: bool) -> None:
    ctx = await _authorize_ws(ws, service_only=service_only)
    if ctx is None:
        return

    await ws.accept()

    meeting_id: str | None = None
    forward_task: asyncio.Task | None = None

    try:
        while True:
            raw = await ws.receive_text()
            try:
                event = json.loads(raw)
            except Exception:
                await ws.send_text(
                    json.dumps(
                        {"event_type": "error", "code": "bad_json", "message": "Невалидный JSON"}
                    )
                )
                continue

            et = event.get("event_type")
            if et != "audio.chunk":
                await ws.send_text(
                    json.dumps(
                        {
                            "event_type": "error",
                            "code": "bad_event",
                            "message": "Неизвестный event_type",
                        }
                    )
                )
                continue

            meeting_id = event.get("meeting_id")
            if not meeting_id:
                await ws.send_text(
                    json.dumps(
                        {
                            "event_type": "error",
                            "code": "no_meeting_id",
                            "message": "meeting_id обязателен",
                        }
                    )
                )
                continue

            # Запускаем forward только один раз, когда получили meeting_id
            if forward_task is None:
                forward_task = asyncio.create_task(_forward_pubsub_to_ws(ws, meeting_id))

            seq = int(event.get("seq", 0))
            content_b64 = event.get("content_b64", "")
            idem_key = event.get("idempotency_key")

            try:
                audio_bytes = b64_decode(content_b64)
            except Exception:
                await ws.send_text(
                    json.dumps(
                        {
                            "event_type": "error",
                            "code": "bad_audio",
                            "message": "content_b64 не декодируется",
                        }
                    )
                )
                continue

            try:
                result = ingest_audio_chunk_bytes(
                    meeting_id=meeting_id,
                    seq=seq,
                    audio_bytes=audio_bytes,
                    idempotency_key=idem_key,
                    idempotency_scope="audio_chunk_ws",
                    idempotency_prefix="ws",
                )
            except Exception as e:
                log.error(
                    "ws_ingest_failed",
                    extra={"meeting_id": meeting_id, "payload": {"err": str(e)[:200]}},
                )
                await ws.send_text(
                    json.dumps(
                        {
                            "event_type": "error",
                            "code": "storage_error",
                            "message": "Ошибка записи чанка",
                        }
                    )
                )
                continue

            if result.is_duplicate:
                continue

    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error(
            "ws_fatal",
            extra={
                "payload": {
                    "err": str(e)[:200],
                    "event": safe_dict(event) if 'event' in locals() else None,
                }
            },
        )
    finally:
        if forward_task:
            forward_task.cancel()


@ws_router.websocket("/ws")
async def websocket_user_endpoint(ws: WebSocket) -> None:
    await _websocket_endpoint_impl(ws, service_only=False)


@ws_router.websocket("/ws/internal")
async def websocket_internal_endpoint(ws: WebSocket) -> None:
    await _websocket_endpoint_impl(ws, service_only=True)
