from __future__ import annotations

import pytest

from interview_analytics_agent.common.config import Settings


def test_settings_reads_api_keys_from_file(monkeypatch, tmp_path) -> None:
    keys_file = tmp_path / "api_keys.txt"
    keys_file.write_text("k1\nk2\n", encoding="utf-8")

    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.setenv("API_KEYS_FILE", str(keys_file))

    settings = Settings(_env_file=None)
    assert settings.api_keys == "k1,k2"


def test_settings_reads_shared_secret_from_file(monkeypatch, tmp_path) -> None:
    secret_file = tmp_path / "jwt_secret.txt"
    secret_file.write_text("secret-value\n", encoding="utf-8")

    monkeypatch.delenv("JWT_SHARED_SECRET", raising=False)
    monkeypatch.setenv("JWT_SHARED_SECRET_FILE", str(secret_file))

    settings = Settings(_env_file=None)
    assert settings.jwt_shared_secret == "secret-value"


def test_settings_raises_on_missing_secret_file(monkeypatch, tmp_path) -> None:
    missing = tmp_path / "missing.txt"
    monkeypatch.setenv("API_KEYS_FILE", str(missing))

    with pytest.raises(RuntimeError):
        Settings(_env_file=None)
