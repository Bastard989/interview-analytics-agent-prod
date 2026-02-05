from __future__ import annotations

import pytest
import requests

from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.common.errors import ErrCode, ProviderError
from interview_analytics_agent.connectors.salutejazz.adapter import SaluteJazzConnector


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = b"" if payload is None else b"{}"

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_adapter_success(monkeypatch) -> None:
    def _fake_request(**kwargs):
        _ = kwargs
        return _Resp(200, {"language": "ru"})

    monkeypatch.setattr("requests.request", _fake_request)
    conn = SaluteJazzConnector(base_url="https://example.test", api_token="tkn", timeout_sec=1)
    data = conn._request("GET", "/api/v1/health")
    assert data["language"] == "ru"


def test_adapter_retries_retryable_status(monkeypatch) -> None:
    s = get_settings()
    prev_retries = s.sberjazz_http_retries
    prev_statuses = s.sberjazz_http_retry_statuses
    s.sberjazz_http_retries = 2
    s.sberjazz_http_retry_statuses = "503"
    calls = {"n": 0}

    def _fake_request(**kwargs):
        _ = kwargs
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp(503, None, "unavailable")
        return _Resp(200, {"ok": True})

    monkeypatch.setattr("requests.request", _fake_request)
    monkeypatch.setattr(
        "interview_analytics_agent.connectors.salutejazz.adapter.time.sleep", lambda _: None
    )
    try:
        conn = SaluteJazzConnector(base_url="https://example.test")
        data = conn._request("GET", "/api/v1/health")
        assert data["ok"] is True
        assert calls["n"] == 2
    finally:
        s.sberjazz_http_retries = prev_retries
        s.sberjazz_http_retry_statuses = prev_statuses


def test_adapter_unauthorized_no_retry(monkeypatch) -> None:
    def _fake_request(**kwargs):
        _ = kwargs
        return _Resp(401, None, "unauthorized")

    monkeypatch.setattr("requests.request", _fake_request)
    conn = SaluteJazzConnector(base_url="https://example.test")
    with pytest.raises(ProviderError) as e:
        conn._request("GET", "/api/v1/health")
    assert "авторизации" in e.value.message
    assert e.value.code == ErrCode.CONNECTOR_AUTH_ERROR
    assert e.value.details and e.value.details.get("status_code") == 401


def test_adapter_timeout_retries_and_fails(monkeypatch) -> None:
    s = get_settings()
    prev_retries = s.sberjazz_http_retries
    s.sberjazz_http_retries = 1
    calls = {"n": 0}

    def _fake_request(**kwargs):
        _ = kwargs
        calls["n"] += 1
        raise requests.Timeout("timeout")

    monkeypatch.setattr("requests.request", _fake_request)
    monkeypatch.setattr(
        "interview_analytics_agent.connectors.salutejazz.adapter.time.sleep", lambda _: None
    )
    try:
        conn = SaluteJazzConnector(base_url="https://example.test")
        with pytest.raises(ProviderError) as e:
            conn._request("GET", "/api/v1/health")
        assert "Таймаут" in e.value.message
        assert e.value.code == ErrCode.CONNECTOR_TIMEOUT
        assert calls["n"] == 2
    finally:
        s.sberjazz_http_retries = prev_retries


def test_adapter_invalid_json_response(monkeypatch) -> None:
    class _BadJsonResp(_Resp):
        def __init__(self) -> None:
            super().__init__(200, payload=None, text="oops")
            self.content = b"not-json"

    def _fake_request(**kwargs):
        _ = kwargs
        return _BadJsonResp()

    monkeypatch.setattr("requests.request", _fake_request)
    conn = SaluteJazzConnector(base_url="https://example.test")
    with pytest.raises(ProviderError) as e:
        conn._request("GET", "/api/v1/health")
    assert e.value.code == ErrCode.CONNECTOR_INVALID_RESPONSE
