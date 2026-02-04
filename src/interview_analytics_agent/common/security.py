"""
Утилиты безопасности и авторизации.

Поддерживаемые режимы (AUTH_MODE):
- api_key — проверка X-API-Key
- jwt     — проверка Bearer JWT через OIDC/JWKS или shared secret
- none    — без авторизации (ТОЛЬКО dev)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import requests

from .config import get_settings
from .errors import UnauthorizedError

try:
    import jwt
except ImportError:  # pragma: no cover
    jwt = None  # type: ignore[assignment]


def _parse_api_keys(raw: str) -> set[str]:
    """
    Разбор строки API_KEYS из ENV в множество.
    """
    return {k.strip() for k in (raw or "").split(",") if k.strip()}


def _parse_csv(raw: str) -> set[str]:
    return {v.strip() for v in (raw or "").split(",") if v.strip()}


@dataclass(frozen=True)
class AuthContext:
    subject: str
    auth_type: str
    claims: dict[str, Any] | None = None


def _jwt_algorithms(raw: str) -> list[str]:
    algos = [a.strip() for a in (raw or "").split(",") if a.strip()]
    return algos or ["RS256"]


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    prefix = "bearer "
    if authorization.lower().startswith(prefix):
        return authorization[len(prefix) :].strip()
    return None


def _is_prod_env(app_env: str | None) -> bool:
    env = (app_env or "").strip().lower()
    return env in {"prod", "production"}


def _claim_values(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {v for v in re.split(r"[,\s]+", value.strip()) if v}
    if isinstance(value, list | tuple | set):
        out: set[str] = set()
        for item in value:
            if item is None:
                continue
            out.update(_claim_values(item))
        return out
    return {str(value)}


def is_service_jwt_claims(claims: dict[str, Any] | None) -> bool:
    if not claims:
        return False

    s = get_settings()
    claim_key = (s.jwt_service_claim_key or "").strip()
    claim_values_allowed = _parse_csv(s.jwt_service_claim_values)
    if claim_key and claim_values_allowed:
        actual = _claim_values(claims.get(claim_key))
        if actual & claim_values_allowed:
            return True

    role_claim = (s.jwt_service_role_claim or "").strip()
    role_values_allowed = _parse_csv(s.jwt_service_allowed_roles)
    if role_claim and role_values_allowed:
        actual_roles = _claim_values(claims.get(role_claim))
        if actual_roles & role_values_allowed:
            return True

    return False


def has_any_service_permission(
    claims: dict[str, Any] | None,
    *,
    required_permissions: set[str],
) -> bool:
    if not required_permissions:
        return True
    if not claims:
        return False
    s = get_settings()
    claim_key = (s.jwt_service_permission_claim or "").strip()
    if not claim_key:
        return False
    actual = _claim_values(claims.get(claim_key))
    return bool(actual & required_permissions)


@lru_cache(maxsize=8)
def _get_jwks_client(jwks_url: str):
    if jwt is None:
        raise UnauthorizedError("JWT библиотека не установлена (PyJWT)")
    return jwt.PyJWKClient(jwks_url)


@lru_cache(maxsize=8)
def _discover_jwks_url(issuer_url: str, timeout_s: int) -> str:
    discovery = issuer_url.rstrip("/") + "/.well-known/openid-configuration"
    try:
        resp = requests.get(discovery, timeout=timeout_s)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise UnauthorizedError("Не удалось получить OIDC discovery", {"err": str(e)}) from e

    jwks = data.get("jwks_uri")
    if not jwks:
        raise UnauthorizedError("OIDC discovery не содержит jwks_uri")
    return str(jwks)


def _verify_jwt(token: str) -> dict[str, Any]:
    if jwt is None:
        raise UnauthorizedError("JWT библиотека не установлена (PyJWT)")

    s = get_settings()
    algos = _jwt_algorithms(s.oidc_algorithms)
    audience = s.oidc_audience
    issuer = s.oidc_issuer_url
    leeway = int(getattr(s, "jwt_clock_skew_sec", 30) or 30)

    options = {"verify_aud": bool(audience)}
    kwargs: dict[str, Any] = {
        "algorithms": algos,
        "options": options,
        "leeway": leeway,
    }
    if audience:
        kwargs["audience"] = audience
    if issuer:
        kwargs["issuer"] = issuer

    secret = (s.jwt_shared_secret or "").strip()
    if secret:
        try:
            return jwt.decode(token, secret, **kwargs)
        except jwt.PyJWTError as e:
            raise UnauthorizedError("JWT не прошёл проверку", {"err": str(e)}) from e

    jwks_url = (s.oidc_jwks_url or "").strip()
    if not jwks_url:
        if not issuer:
            raise UnauthorizedError("JWT/OIDC не настроен: укажи OIDC_JWKS_URL или OIDC_ISSUER_URL")
        timeout_s = int(getattr(s, "oidc_discovery_timeout_sec", 5) or 5)
        jwks_url = _discover_jwks_url(issuer, timeout_s)

    try:
        key = _get_jwks_client(jwks_url).get_signing_key_from_jwt(token).key
        return jwt.decode(token, key=key, **kwargs)
    except jwt.PyJWTError as e:
        raise UnauthorizedError("JWT не прошёл проверку", {"err": str(e)}) from e


def require_auth(*, authorization: str | None, x_api_key: str | None) -> AuthContext:
    """
    Универсальная проверка авторизации:
    - AUTH_MODE=none: без проверки (dev)
    - AUTH_MODE=api_key: только X-API-Key / SERVICE_API_KEYS
    - AUTH_MODE=jwt: JWT (Bearer) + опциональный service API key fallback
    """
    settings = get_settings()
    mode = (settings.auth_mode or "api_key").lower().strip()

    if mode == "none":
        if _is_prod_env(getattr(settings, "app_env", None)):
            raise UnauthorizedError("AUTH_MODE=none запрещён в APP_ENV=prod")
        return AuthContext(subject="anonymous", auth_type="none")

    user_keys = _parse_api_keys(settings.api_keys)
    service_keys = _parse_api_keys(getattr(settings, "service_api_keys", ""))
    combined_keys = user_keys | service_keys
    has_valid_key = bool(x_api_key and x_api_key in combined_keys)
    has_valid_service_key = bool(x_api_key and x_api_key in service_keys)

    if mode == "api_key":
        if not has_valid_key:
            raise UnauthorizedError("Неверный API ключ")
        if has_valid_service_key:
            return AuthContext(subject="service", auth_type="service_api_key")
        return AuthContext(subject="user", auth_type="user_api_key")

    if mode != "jwt":
        raise UnauthorizedError("Неизвестный режим авторизации")

    token = _extract_bearer(authorization)
    if token:
        claims = _verify_jwt(token)
        sub = str(claims.get("sub") or claims.get("client_id") or "jwt_subject")
        return AuthContext(subject=sub, auth_type="jwt", claims=claims)

    is_prod = _is_prod_env(getattr(settings, "app_env", None))
    allow_key_fallback = bool(getattr(settings, "allow_service_api_key_in_jwt_mode", True))
    if allow_key_fallback and (not is_prod) and has_valid_service_key:
        return AuthContext(subject="service", auth_type="service_api_key")

    if is_prod and has_valid_service_key:
        raise UnauthorizedError(
            "В APP_ENV=prod требуется Bearer JWT; fallback service API key отключён"
        )

    if is_prod:
        raise UnauthorizedError("Требуется Bearer JWT")

    raise UnauthorizedError("Требуется Bearer JWT или service API key")


def require_api_key(x_api_key: str | None) -> None:
    """
    Совместимость со старым API.
    """
    require_auth(authorization=None, x_api_key=x_api_key)
