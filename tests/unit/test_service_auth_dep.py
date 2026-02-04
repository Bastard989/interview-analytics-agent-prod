from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from apps.api_gateway.deps import service_auth_dep, service_auth_read_dep, service_auth_write_dep
from interview_analytics_agent.common.config import get_settings

jwt = pytest.importorskip("jwt")


@pytest.fixture()
def auth_settings():
    s = get_settings()
    keys = [
        "app_env",
        "auth_mode",
        "api_keys",
        "service_api_keys",
        "security_audit_db_enabled",
        "allow_service_api_key_in_jwt_mode",
        "oidc_issuer_url",
        "oidc_jwks_url",
        "oidc_audience",
        "oidc_algorithms",
        "jwt_shared_secret",
        "jwt_clock_skew_sec",
        "jwt_service_claim_key",
        "jwt_service_claim_values",
        "jwt_service_role_claim",
        "jwt_service_allowed_roles",
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


def _build_hs256_token(
    *,
    secret: str,
    sub: str = "user-1",
    extra_claims: dict | None = None,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "iss": "https://issuer.local",
        "aud": "interview-agent",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return str(jwt.encode(payload, secret, algorithm="HS256"))


def _make_request(path: str = "/v1/admin/queues/health") -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_service_dep_allows_service_key_in_jwt_mode(auth_settings) -> None:
    auth_settings.auth_mode = "jwt"
    auth_settings.service_api_keys = "svc-1"
    auth_settings.allow_service_api_key_in_jwt_mode = True

    ctx = service_auth_dep(request=_make_request(), authorization=None, x_api_key="svc-1")
    assert ctx.auth_type == "service_api_key"


def test_service_dep_rejects_user_jwt(auth_settings) -> None:
    auth_settings.auth_mode = "jwt"
    auth_settings.jwt_shared_secret = "test-secret"
    auth_settings.oidc_algorithms = "HS256"
    auth_settings.oidc_issuer_url = "https://issuer.local"
    auth_settings.oidc_audience = "interview-agent"

    token = _build_hs256_token(secret="test-secret", sub="candidate-42")
    with pytest.raises(HTTPException) as e:
        service_auth_dep(request=_make_request(), authorization=f"Bearer {token}", x_api_key=None)
    assert e.value.status_code == 403


def test_service_dep_allows_service_jwt_by_token_type_claim(auth_settings) -> None:
    auth_settings.auth_mode = "jwt"
    auth_settings.jwt_shared_secret = "test-secret"
    auth_settings.oidc_algorithms = "HS256"
    auth_settings.oidc_issuer_url = "https://issuer.local"
    auth_settings.oidc_audience = "interview-agent"
    auth_settings.jwt_service_claim_key = "token_type"
    auth_settings.jwt_service_claim_values = "service,m2m"

    token = _build_hs256_token(
        secret="test-secret",
        sub="svc-account-1",
        extra_claims={"token_type": "service"},
    )
    ctx = service_auth_dep(
        request=_make_request(),
        authorization=f"Bearer {token}",
        x_api_key=None,
    )
    assert ctx.auth_type == "jwt"


def test_service_dep_allows_service_jwt_by_roles_claim(auth_settings) -> None:
    auth_settings.auth_mode = "jwt"
    auth_settings.jwt_shared_secret = "test-secret"
    auth_settings.oidc_algorithms = "HS256"
    auth_settings.oidc_issuer_url = "https://issuer.local"
    auth_settings.oidc_audience = "interview-agent"
    auth_settings.jwt_service_claim_key = "token_type"
    auth_settings.jwt_service_claim_values = "service"
    auth_settings.jwt_service_role_claim = "roles"
    auth_settings.jwt_service_allowed_roles = "internal_service,admin"

    token = _build_hs256_token(
        secret="test-secret",
        sub="svc-account-2",
        extra_claims={"roles": ["internal_service"]},
    )
    ctx = service_auth_dep(
        request=_make_request(),
        authorization=f"Bearer {token}",
        x_api_key=None,
    )
    assert ctx.auth_type == "jwt"


def test_service_dep_allows_service_key_in_api_key_mode(auth_settings) -> None:
    auth_settings.auth_mode = "api_key"
    auth_settings.service_api_keys = "svc-1"
    auth_settings.api_keys = "user-1"

    ctx = service_auth_dep(request=_make_request(), authorization=None, x_api_key="svc-1")
    assert ctx.auth_type == "service_api_key"


def test_service_dep_rejects_user_key_in_api_key_mode(auth_settings) -> None:
    auth_settings.auth_mode = "api_key"
    auth_settings.api_keys = "user-1"
    auth_settings.service_api_keys = "svc-1"

    with pytest.raises(HTTPException) as e:
        service_auth_dep(request=_make_request(), authorization=None, x_api_key="user-1")
    assert e.value.status_code == 403


def test_service_read_dep_requires_scope_for_service_jwt(auth_settings) -> None:
    auth_settings.auth_mode = "jwt"
    auth_settings.jwt_shared_secret = "test-secret"
    auth_settings.oidc_algorithms = "HS256"
    auth_settings.oidc_issuer_url = "https://issuer.local"
    auth_settings.oidc_audience = "interview-agent"
    auth_settings.jwt_service_claim_key = "token_type"
    auth_settings.jwt_service_claim_values = "service,m2m"
    auth_settings.jwt_service_permission_claim = "scope"
    auth_settings.jwt_service_required_scopes_admin_read = "agent.admin.read"

    token = _build_hs256_token(
        secret="test-secret",
        sub="svc-account-3",
        extra_claims={"token_type": "service", "scope": "agent.ws.internal"},
    )
    with pytest.raises(HTTPException) as e:
        service_auth_read_dep(
            request=_make_request(),
            authorization=f"Bearer {token}",
            x_api_key=None,
        )
    assert e.value.status_code == 403


def test_service_write_dep_allows_scope_for_service_jwt(auth_settings) -> None:
    auth_settings.auth_mode = "jwt"
    auth_settings.jwt_shared_secret = "test-secret"
    auth_settings.oidc_algorithms = "HS256"
    auth_settings.oidc_issuer_url = "https://issuer.local"
    auth_settings.oidc_audience = "interview-agent"
    auth_settings.jwt_service_claim_key = "token_type"
    auth_settings.jwt_service_claim_values = "service,m2m"
    auth_settings.jwt_service_permission_claim = "scope"
    auth_settings.jwt_service_required_scopes_admin_write = "agent.admin.write"

    token = _build_hs256_token(
        secret="test-secret",
        sub="svc-account-4",
        extra_claims={"token_type": "service", "scope": "agent.admin.write"},
    )
    ctx = service_auth_write_dep(
        request=_make_request(path="/v1/admin/connectors/sberjazz/m-1/join"),
        authorization=f"Bearer {token}",
        x_api_key=None,
    )
    assert ctx.auth_type == "jwt"
