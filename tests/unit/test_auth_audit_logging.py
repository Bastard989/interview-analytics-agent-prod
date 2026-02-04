from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from apps.api_gateway.deps import auth_dep, service_auth_dep
from interview_analytics_agent.common.config import get_settings


def _make_request(*, path: str, method: str = "GET") -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


@pytest.fixture()
def auth_settings():
    s = get_settings()
    keys = [
        "auth_mode",
        "api_keys",
        "service_api_keys",
        "security_audit_db_enabled",
        "allow_service_api_key_in_jwt_mode",
        "jwt_service_permission_claim",
        "jwt_service_required_scopes_admin_read",
        "jwt_service_required_scopes_admin_write",
    ]
    snapshot = {k: getattr(s, k) for k in keys}
    try:
        s.security_audit_db_enabled = False
        yield s
    finally:
        for k, v in snapshot.items():
            setattr(s, k, v)


def test_auth_dep_logs_allow(caplog, auth_settings) -> None:
    auth_settings.auth_mode = "api_key"
    auth_settings.api_keys = "user-1"

    caplog.set_level(logging.INFO, logger="interview-analytics-agent")
    req = _make_request(path="/v1/meetings/start", method="POST")
    ctx = auth_dep(authorization=None, x_api_key="user-1", request=req)

    assert ctx.auth_type == "user_api_key"
    rec = next(r for r in caplog.records if r.msg == "security_audit_allow")
    assert rec.payload["endpoint"] == "/v1/meetings/start"
    assert rec.payload["method"] == "POST"
    assert rec.payload["reason"] == "auth_ok"
    assert rec.payload["auth_type"] == "user_api_key"


def test_auth_dep_logs_deny_401(caplog, auth_settings) -> None:
    auth_settings.auth_mode = "api_key"
    auth_settings.api_keys = "user-1"

    caplog.set_level(logging.INFO, logger="interview-analytics-agent")
    req = _make_request(path="/v1/meetings/start", method="POST")
    with pytest.raises(HTTPException) as e:
        auth_dep(authorization=None, x_api_key="bad", request=req)
    assert e.value.status_code == 401

    rec = next(r for r in caplog.records if r.msg == "security_audit_deny")
    assert rec.payload["endpoint"] == "/v1/meetings/start"
    assert rec.payload["status_code"] == 401
    assert rec.payload["error_code"] == "unauthorized"


def test_service_auth_dep_logs_deny_403_with_reason(caplog, auth_settings) -> None:
    auth_settings.auth_mode = "api_key"
    auth_settings.api_keys = "user-1"
    auth_settings.service_api_keys = "svc-1"

    caplog.set_level(logging.INFO, logger="interview-analytics-agent")
    req = _make_request(path="/v1/admin/queues/health")
    with pytest.raises(HTTPException) as e:
        service_auth_dep(authorization=None, x_api_key="user-1", request=req)
    assert e.value.status_code == 403

    denies = [r for r in caplog.records if r.msg == "security_audit_deny"]
    assert denies
    rec = denies[-1]
    assert rec.payload["endpoint"] == "/v1/admin/queues/health"
    assert rec.payload["reason"] == "not_service_identity"
    assert rec.payload["status_code"] == 403
    assert rec.payload["auth_type"] == "user_api_key"
