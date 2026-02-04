"""
Runtime readiness checks for production rollout.
"""

from __future__ import annotations

from dataclasses import dataclass

from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.common.logging import get_project_logger

log = get_project_logger()


@dataclass
class ReadinessIssue:
    severity: str  # error|warning
    code: str
    message: str


@dataclass
class ReadinessState:
    ready: bool
    issues: list[ReadinessIssue]


def _is_prod_env(app_env: str | None) -> bool:
    env = (app_env or "").strip().lower()
    return env in {"prod", "production"}


def evaluate_readiness() -> ReadinessState:
    s = get_settings()
    issues: list[ReadinessIssue] = []
    is_prod = _is_prod_env(s.app_env)

    if (s.auth_mode or "").strip().lower() == "api_key" and not (s.api_keys or "").strip():
        issues.append(
            ReadinessIssue(
                severity="error",
                code="auth_api_keys_empty",
                message="AUTH_MODE=api_key требует непустой API_KEYS",
            )
        )

    if not (s.service_api_keys or "").strip():
        issues.append(
            ReadinessIssue(
                severity="warning",
                code="service_api_keys_empty",
                message="SERVICE_API_KEYS пустой, service fallback не будет работать",
            )
        )

    provider = (s.meeting_connector_provider or "").strip().lower()
    if provider == "sberjazz" and not (s.sberjazz_api_base or "").strip():
        issues.append(
            ReadinessIssue(
                severity="error",
                code="sberjazz_api_base_empty",
                message="MEETING_CONNECTOR_PROVIDER=sberjazz требует SBERJAZZ_API_BASE",
            )
        )

    if is_prod:
        auth_mode = (s.auth_mode or "").strip().lower()
        if auth_mode == "none":
            issues.append(
                ReadinessIssue(
                    severity="error",
                    code="auth_none_in_prod",
                    message="AUTH_MODE=none запрещен в prod",
                )
            )
        if auth_mode == "jwt":
            if bool(getattr(s, "allow_service_api_key_in_jwt_mode", True)):
                issues.append(
                    ReadinessIssue(
                        severity="warning",
                        code="jwt_service_key_fallback_enabled",
                        message="ALLOW_SERVICE_API_KEY_IN_JWT_MODE=true будет проигнорирован в prod",
                    )
                )
            if not (s.oidc_issuer_url or "").strip() and not (s.oidc_jwks_url or "").strip():
                issues.append(
                    ReadinessIssue(
                        severity="error",
                        code="oidc_not_configured",
                        message="AUTH_MODE=jwt требует OIDC_ISSUER_URL или OIDC_JWKS_URL",
                    )
                )
            if (s.jwt_shared_secret or "").strip():
                issues.append(
                    ReadinessIssue(
                        severity="warning",
                        code="jwt_shared_secret_set",
                        message="JWT_SHARED_SECRET задан; в prod лучше использовать OIDC/JWKS",
                    )
                )

        if bool(getattr(s, "storage_require_shared_in_prod", True)) and (
            (s.storage_mode or "").strip().lower() != "shared_fs"
        ):
            issues.append(
                ReadinessIssue(
                    severity="error",
                    code="storage_not_shared_fs",
                    message="В prod требуется STORAGE_MODE=shared_fs",
                )
            )

        if "*" in (s.cors_allowed_origins or ""):
            issues.append(
                ReadinessIssue(
                    severity="error",
                    code="cors_wildcard_in_prod",
                    message="CORS wildcard '*' запрещен в prod",
                )
            )

        if provider == "sberjazz_mock":
            issues.append(
                ReadinessIssue(
                    severity="warning",
                    code="mock_connector_in_prod",
                    message="В prod используется sberjazz_mock; рекомендуется real sberjazz",
                )
            )

    ready = all(i.severity != "error" for i in issues)
    return ReadinessState(ready=ready, issues=issues)


def enforce_startup_readiness(*, service_name: str) -> ReadinessState:
    s = get_settings()
    state = evaluate_readiness()
    errors = [i for i in state.issues if i.severity == "error"]

    if errors:
        log.error(
            "startup_readiness_failed",
            extra={
                "payload": {
                    "service": service_name,
                    "app_env": s.app_env,
                    "error_codes": [e.code for e in errors],
                }
            },
        )
    else:
        log.info(
            "startup_readiness_ok",
            extra={"payload": {"service": service_name, "app_env": s.app_env}},
        )

    should_fail_fast = _is_prod_env(s.app_env) and bool(
        getattr(s, "readiness_fail_fast_in_prod", True)
    )
    if should_fail_fast and errors:
        msg = ", ".join(e.code for e in errors)
        raise RuntimeError(f"startup readiness failed for {service_name}: {msg}")
    return state
