from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.common.errors import UnauthorizedError
from interview_analytics_agent.common.security import require_auth

jwt = pytest.importorskip("jwt")


@pytest.fixture()
def auth_settings():
    s = get_settings()
    keys = [
        "app_env",
        "auth_mode",
        "api_keys",
        "service_api_keys",
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
        "jwt_service_required_scopes_ws_internal",
    ]
    snapshot = {k: getattr(s, k) for k in keys}
    try:
        yield s
    finally:
        for k, v in snapshot.items():
            setattr(s, k, v)


def _build_hs256_token(*, secret: str, sub: str = "user-1") -> str:
    now = datetime.now(timezone.utc)  # noqa: UP017 - local pytest runs on Python 3.10
    payload = {
        "sub": sub,
        "iss": "https://issuer.local",
        "aud": "interview-agent",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    return str(jwt.encode(payload, secret, algorithm="HS256"))


def test_auth_none_mode_allows_request(auth_settings) -> None:
    auth_settings.app_env = "dev"
    auth_settings.auth_mode = "none"
    ctx = require_auth(authorization=None, x_api_key=None)
    assert ctx.auth_type == "none"


def test_auth_none_mode_rejected_in_prod(auth_settings) -> None:
    auth_settings.app_env = "prod"
    auth_settings.auth_mode = "none"
    with pytest.raises(UnauthorizedError):
        require_auth(authorization=None, x_api_key=None)


def test_auth_api_key_mode_rejects_invalid_key(auth_settings) -> None:
    auth_settings.auth_mode = "api_key"
    auth_settings.api_keys = "k1,k2"
    with pytest.raises(UnauthorizedError):
        require_auth(authorization=None, x_api_key="bad")


def test_auth_api_key_mode_accepts_valid_key(auth_settings) -> None:
    auth_settings.auth_mode = "api_key"
    auth_settings.api_keys = "k1,k2"
    ctx = require_auth(authorization=None, x_api_key="k2")
    assert ctx.auth_type == "user_api_key"


def test_auth_api_key_mode_marks_service_key(auth_settings) -> None:
    auth_settings.auth_mode = "api_key"
    auth_settings.api_keys = "k1"
    auth_settings.service_api_keys = "svc-1"
    ctx = require_auth(authorization=None, x_api_key="svc-1")
    assert ctx.auth_type == "service_api_key"


def test_auth_jwt_mode_validates_bearer_token(auth_settings) -> None:
    auth_settings.auth_mode = "jwt"
    auth_settings.jwt_shared_secret = "test-secret"
    auth_settings.oidc_algorithms = "HS256"
    auth_settings.oidc_issuer_url = "https://issuer.local"
    auth_settings.oidc_audience = "interview-agent"

    token = _build_hs256_token(secret="test-secret", sub="candidate-42")
    ctx = require_auth(authorization=f"Bearer {token}", x_api_key=None)

    assert ctx.auth_type == "jwt"
    assert ctx.subject == "candidate-42"
    assert isinstance(ctx.claims, dict)


def test_auth_jwt_mode_allows_service_key_fallback(auth_settings) -> None:
    auth_settings.auth_mode = "jwt"
    auth_settings.allow_service_api_key_in_jwt_mode = True
    auth_settings.service_api_keys = "svc-1"

    ctx = require_auth(authorization=None, x_api_key="svc-1")
    assert ctx.auth_type == "service_api_key"


def test_auth_jwt_mode_rejects_user_api_key_in_fallback(auth_settings) -> None:
    auth_settings.auth_mode = "jwt"
    auth_settings.allow_service_api_key_in_jwt_mode = True
    auth_settings.api_keys = "user-1"
    auth_settings.service_api_keys = "svc-1"

    with pytest.raises(UnauthorizedError):
        require_auth(authorization=None, x_api_key="user-1")


def test_auth_jwt_mode_disables_service_key_fallback_in_prod(auth_settings) -> None:
    auth_settings.app_env = "prod"
    auth_settings.auth_mode = "jwt"
    auth_settings.allow_service_api_key_in_jwt_mode = True
    auth_settings.service_api_keys = "svc-1"

    with pytest.raises(UnauthorizedError, match="Bearer JWT"):
        require_auth(authorization=None, x_api_key="svc-1")
