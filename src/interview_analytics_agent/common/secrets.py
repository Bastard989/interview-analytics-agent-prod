"""
External secrets provider loader (Vault).

Loads secrets into environment before Settings initialization.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import requests

_log = logging.getLogger("interview-analytics-agent")


def maybe_load_external_secrets() -> None:
    provider = (os.getenv("SECRETS_PROVIDER") or "").strip().lower()
    if provider in {"", "none"}:
        return
    if provider == "vault":
        _load_vault()
        return
    raise RuntimeError(f"Unsupported SECRETS_PROVIDER={provider}")


def _read_token_from_file(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except Exception as e:
        _log.error(
            "vault_token_file_read_failed",
            extra={"payload": {"path": path, "error": str(e)[:200]}},
        )
        raise


def _parse_field_map(raw: str | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not raw:
        return mapping
    for item in re.split(r"[\n,]+", raw):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            continue
        env_key, secret_key = (p.strip() for p in item.split("=", 1))
        if env_key and secret_key:
            mapping[env_key] = secret_key
    return mapping


def _vault_request_headers(token: str, namespace: str | None) -> dict[str, str]:
    headers = {"X-Vault-Token": token}
    if namespace:
        headers["X-Vault-Namespace"] = namespace
    return headers


def _load_vault() -> None:
    addr = (os.getenv("VAULT_ADDR") or "").strip().rstrip("/")
    token = (os.getenv("VAULT_TOKEN") or "").strip()
    token = token or (_read_token_from_file(os.getenv("VAULT_TOKEN_FILE")) or "")
    mount = (os.getenv("VAULT_KV_MOUNT") or "secret").strip().strip("/")
    path = (os.getenv("VAULT_SECRET_PATH") or "").strip().strip("/")
    namespace = (os.getenv("VAULT_NAMESPACE") or "").strip() or None
    verify = (os.getenv("VAULT_SKIP_VERIFY") or "").strip().lower() not in {"1", "true"}
    timeout = int(os.getenv("VAULT_TIMEOUT_SEC") or 5)
    version = (os.getenv("VAULT_KV_VERSION") or "2").strip()
    field_map = _parse_field_map(os.getenv("VAULT_FIELD_MAP"))

    if not addr or not token or not path:
        raise RuntimeError("Vault secrets require VAULT_ADDR, VAULT_TOKEN and VAULT_SECRET_PATH")

    if version not in {"1", "2"}:
        raise RuntimeError("VAULT_KV_VERSION must be '1' or '2'")

    url = f"{addr}/v1/{mount}/data/{path}" if version == "2" else f"{addr}/v1/{mount}/{path}"

    try:
        resp = requests.get(
            url,
            headers=_vault_request_headers(token, namespace),
            timeout=timeout,
            verify=verify,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise RuntimeError(f"Vault secrets fetch failed: {e}") from e

    if version == "2":
        secret_data = (data or {}).get("data", {}).get("data", {})
    else:
        secret_data = (data or {}).get("data", {})

    if not isinstance(secret_data, dict):
        raise RuntimeError("Vault secrets payload is not a dict")

    if not field_map:
        raise RuntimeError("VAULT_FIELD_MAP is required to map secret fields to env")

    updated = 0
    for env_key, secret_key in field_map.items():
        if env_key in os.environ and os.environ[env_key].strip():
            continue
        value = secret_data.get(secret_key)
        if value is None:
            continue
        if isinstance(value, dict | list):
            os.environ[env_key] = json.dumps(value)
        else:
            os.environ[env_key] = str(value)
        updated += 1

    _log.info(
        "vault_secrets_loaded",
        extra={"payload": {"updated": updated, "path": path, "mount": mount}},
    )
