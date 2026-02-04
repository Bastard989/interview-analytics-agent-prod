from __future__ import annotations

import pytest

from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.services.readiness_service import (
    enforce_startup_readiness,
    evaluate_readiness,
)


def test_readiness_prod_fails_on_none_auth_and_local_storage() -> None:
    s = get_settings()
    snapshot = (
        s.app_env,
        s.auth_mode,
        s.storage_mode,
        s.storage_require_shared_in_prod,
        s.cors_allowed_origins,
    )
    try:
        s.app_env = "prod"
        s.auth_mode = "none"
        s.storage_mode = "local_fs"
        s.storage_require_shared_in_prod = True
        s.cors_allowed_origins = "*"
        state = evaluate_readiness()
        codes = {i.code for i in state.issues}
        assert state.ready is False
        assert "auth_none_in_prod" in codes
        assert "storage_not_shared_fs" in codes
        assert "cors_wildcard_in_prod" in codes
    finally:
        (
            s.app_env,
            s.auth_mode,
            s.storage_mode,
            s.storage_require_shared_in_prod,
            s.cors_allowed_origins,
        ) = snapshot


def test_readiness_dev_allows_defaults() -> None:
    s = get_settings()
    snapshot = (
        s.app_env,
        s.auth_mode,
        s.storage_mode,
        s.api_keys,
    )
    try:
        s.app_env = "dev"
        s.auth_mode = "api_key"
        s.storage_mode = "local_fs"
        s.api_keys = "dev-key"
        state = evaluate_readiness()
        # warning'и допустимы, важно что нет ошибок.
        assert state.ready is True
    finally:
        (
            s.app_env,
            s.auth_mode,
            s.storage_mode,
            s.api_keys,
        ) = snapshot


def test_startup_readiness_fail_fast_in_prod() -> None:
    s = get_settings()
    snapshot = (
        s.app_env,
        s.auth_mode,
        s.readiness_fail_fast_in_prod,
    )
    try:
        s.app_env = "prod"
        s.auth_mode = "none"
        s.readiness_fail_fast_in_prod = True
        with pytest.raises(RuntimeError, match="auth_none_in_prod"):
            enforce_startup_readiness(service_name="api-gateway")
    finally:
        (
            s.app_env,
            s.auth_mode,
            s.readiness_fail_fast_in_prod,
        ) = snapshot


def test_startup_readiness_no_fail_fast_in_prod() -> None:
    s = get_settings()
    snapshot = (
        s.app_env,
        s.auth_mode,
        s.readiness_fail_fast_in_prod,
    )
    try:
        s.app_env = "prod"
        s.auth_mode = "none"
        s.readiness_fail_fast_in_prod = False
        state = enforce_startup_readiness(service_name="worker-stt")
        assert state.ready is False
        assert any(i.code == "auth_none_in_prod" for i in state.issues)
    finally:
        (
            s.app_env,
            s.auth_mode,
            s.readiness_fail_fast_in_prod,
        ) = snapshot


def test_readiness_prod_jwt_fallback_enabled_is_warning() -> None:
    s = get_settings()
    snapshot = (
        s.app_env,
        s.auth_mode,
        s.allow_service_api_key_in_jwt_mode,
        s.oidc_issuer_url,
        s.oidc_jwks_url,
    )
    try:
        s.app_env = "prod"
        s.auth_mode = "jwt"
        s.allow_service_api_key_in_jwt_mode = True
        s.oidc_issuer_url = "https://issuer.local"
        s.oidc_jwks_url = None
        state = evaluate_readiness()
        issue = next(
            (i for i in state.issues if i.code == "jwt_service_key_fallback_enabled"), None
        )
        assert issue is not None
        assert issue.severity == "warning"
    finally:
        (
            s.app_env,
            s.auth_mode,
            s.allow_service_api_key_in_jwt_mode,
            s.oidc_issuer_url,
            s.oidc_jwks_url,
        ) = snapshot
