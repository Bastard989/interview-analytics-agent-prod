"""
Tenant enforcement helpers (JWT only).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.common.errors import ErrCode
from interview_analytics_agent.common.security import AuthContext, is_service_jwt_claims


def tenant_enforcement_enabled() -> bool:
    return bool(getattr(get_settings(), "tenant_enforcement_enabled", False))


def _tenant_claim_key() -> str:
    key = (get_settings().tenant_claim_key or "").strip()
    return key or "tenant_id"


def _tenant_context_key() -> str:
    key = (get_settings().tenant_context_key or "").strip()
    return key or "tenant_id"


def _normalize_tenant_id(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list | tuple | set):
        for item in value:
            if item is None:
                continue
            return str(item)
        return None
    return str(value)


def resolve_tenant_id(ctx: AuthContext) -> str | None:
    if not tenant_enforcement_enabled():
        return None
    if ctx.auth_type != "jwt":
        return None
    if is_service_jwt_claims(ctx.claims):
        return None
    claims = ctx.claims or {}
    return _normalize_tenant_id(claims.get(_tenant_claim_key()))


def apply_tenant_to_context(ctx: AuthContext, context: dict | None) -> dict:
    if not tenant_enforcement_enabled():
        return context or {}

    tenant_id = resolve_tenant_id(ctx)
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": ErrCode.FORBIDDEN, "message": "Tenant claim отсутствует"},
        )

    key = _tenant_context_key()
    out = dict(context or {})
    if key in out and out[key] not in (None, "", tenant_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": ErrCode.FORBIDDEN, "message": "Tenant mismatch"},
        )
    out[key] = tenant_id
    return out


def enforce_meeting_access(ctx: AuthContext, meeting_context: dict | None) -> None:
    if not tenant_enforcement_enabled():
        return

    tenant_id = resolve_tenant_id(ctx)
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": ErrCode.FORBIDDEN, "message": "Tenant claim отсутствует"},
        )

    key = _tenant_context_key()
    meeting_tenant = (meeting_context or {}).get(key)
    if meeting_tenant != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": ErrCode.FORBIDDEN, "message": "Tenant mismatch"},
        )
