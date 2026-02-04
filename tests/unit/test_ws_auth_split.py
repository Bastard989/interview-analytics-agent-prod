from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from apps.api_gateway.main import app
from interview_analytics_agent.common.config import get_settings

jwt = pytest.importorskip("jwt")


@pytest.fixture()
def auth_settings():
    s = get_settings()
    keys = [
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
        "jwt_service_required_scopes_ws_internal",
    ]
    snapshot = {k: getattr(s, k) for k in keys}
    try:
        yield s
    finally:
        for k, v in snapshot.items():
            setattr(s, k, v)


def _build_hs256_token(*, secret: str, sub: str, extra_claims: dict | None = None) -> str:
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


def test_ws_user_allows_user_api_key(auth_settings) -> None:
    auth_settings.auth_mode = "api_key"
    auth_settings.api_keys = "user-1"
    auth_settings.service_api_keys = "svc-1"

    client = TestClient(app)
    with client.websocket_connect("/v1/ws", headers={"X-API-Key": "user-1"}):
        pass


def test_ws_user_denies_service_api_key(auth_settings) -> None:
    auth_settings.auth_mode = "api_key"
    auth_settings.api_keys = "user-1"
    auth_settings.service_api_keys = "svc-1"

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as e, client.websocket_connect(
        "/v1/ws", headers={"X-API-Key": "svc-1"}
    ):
        pass
    assert e.value.code == 1008


def test_ws_internal_allows_service_api_key(auth_settings) -> None:
    auth_settings.auth_mode = "api_key"
    auth_settings.api_keys = "user-1"
    auth_settings.service_api_keys = "svc-1"

    client = TestClient(app)
    with client.websocket_connect("/v1/ws/internal", headers={"X-API-Key": "svc-1"}):
        pass


def test_ws_internal_denies_user_api_key(auth_settings) -> None:
    auth_settings.auth_mode = "api_key"
    auth_settings.api_keys = "user-1"
    auth_settings.service_api_keys = "svc-1"

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as e, client.websocket_connect(
        "/v1/ws/internal", headers={"X-API-Key": "user-1"}
    ):
        pass
    assert e.value.code == 1008


def test_ws_internal_allows_service_jwt(auth_settings) -> None:
    auth_settings.auth_mode = "jwt"
    auth_settings.jwt_shared_secret = "test-secret"
    auth_settings.oidc_algorithms = "HS256"
    auth_settings.oidc_issuer_url = "https://issuer.local"
    auth_settings.oidc_audience = "interview-agent"
    auth_settings.jwt_service_claim_key = "token_type"
    auth_settings.jwt_service_claim_values = "service,m2m"
    auth_settings.jwt_service_permission_claim = "scope"
    auth_settings.jwt_service_required_scopes_ws_internal = "agent.ws.internal"

    token = _build_hs256_token(
        secret="test-secret",
        sub="svc-account-1",
        extra_claims={"token_type": "service", "scope": "agent.ws.internal"},
    )
    client = TestClient(app)
    with client.websocket_connect("/v1/ws/internal", headers={"Authorization": f"Bearer {token}"}):
        pass


def test_ws_internal_denies_service_jwt_without_scope(auth_settings) -> None:
    auth_settings.auth_mode = "jwt"
    auth_settings.jwt_shared_secret = "test-secret"
    auth_settings.oidc_algorithms = "HS256"
    auth_settings.oidc_issuer_url = "https://issuer.local"
    auth_settings.oidc_audience = "interview-agent"
    auth_settings.jwt_service_claim_key = "token_type"
    auth_settings.jwt_service_claim_values = "service,m2m"
    auth_settings.jwt_service_permission_claim = "scope"
    auth_settings.jwt_service_required_scopes_ws_internal = "agent.ws.internal"

    token = _build_hs256_token(
        secret="test-secret",
        sub="svc-account-1",
        extra_claims={"token_type": "service", "scope": "agent.admin.read"},
    )
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as e, client.websocket_connect(
        "/v1/ws/internal", headers={"Authorization": f"Bearer {token}"}
    ):
        pass
    assert e.value.code == 1008


def test_ws_user_denies_service_jwt(auth_settings) -> None:
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
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as e, client.websocket_connect(
        "/v1/ws", headers={"Authorization": f"Bearer {token}"}
    ):
        pass
    assert e.value.code == 1008
