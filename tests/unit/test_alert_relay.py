from __future__ import annotations

import requests
from fastapi.testclient import TestClient

import apps.alert_relay.main as relay


class _Resp:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")


def test_health_reports_channel_targets(monkeypatch) -> None:
    monkeypatch.setenv("ALERT_RELAY_WARNING_TARGET_URL", "https://example.test/warn")
    monkeypatch.delenv("ALERT_RELAY_WARNING_SHADOW_URL", raising=False)
    monkeypatch.setenv("ALERT_RELAY_RETRIES", "3")
    monkeypatch.setenv("ALERT_RELAY_RETRY_BACKOFF_MS", "250")
    monkeypatch.setenv("ALERT_RELAY_RETRY_STATUSES", "429,503")
    client = TestClient(relay.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["channels"]["warning"]["target_set"] is True
    assert data["channels"]["warning"]["shadow_set"] is False
    assert data["retries"] == 3
    assert data["retry_backoff_sec"] == 0.25
    assert data["retry_statuses"] == [429, 503]


def test_webhook_forwards_to_target_and_shadow(monkeypatch) -> None:
    monkeypatch.setenv("ALERT_RELAY_WARNING_TARGET_URL", "https://example.test/warn")
    monkeypatch.setenv("ALERT_RELAY_WARNING_SHADOW_URL", "https://example.test/warn-shadow")
    monkeypatch.setenv("ALERT_RELAY_FAIL_ON_ERROR", "true")
    calls: list[str] = []

    def _fake_post(url: str, json: dict, timeout: int):
        _ = json, timeout
        calls.append(url)
        return _Resp(200)

    monkeypatch.setattr(relay.requests, "post", _fake_post)
    client = TestClient(relay.app)
    resp = client.post("/webhook/warning", json={"alerts": [{"labels": {"severity": "warning"}}]})
    assert resp.status_code == 200
    assert resp.json()["forwarded"] == 2
    assert calls == ["https://example.test/warn", "https://example.test/warn-shadow"]


def test_webhook_fail_on_error_true_returns_502(monkeypatch) -> None:
    monkeypatch.setenv("ALERT_RELAY_CRITICAL_TARGET_URL", "https://example.test/critical")
    monkeypatch.setenv("ALERT_RELAY_FAIL_ON_ERROR", "true")

    def _fake_post(url: str, json: dict, timeout: int):
        _ = url, json, timeout
        raise requests.ConnectionError("down")

    monkeypatch.setattr(relay.requests, "post", _fake_post)
    client = TestClient(relay.app)
    resp = client.post("/webhook/critical", json={"alerts": []})
    assert resp.status_code == 502


def test_webhook_fail_on_error_false_returns_ok(monkeypatch) -> None:
    monkeypatch.setenv("ALERT_RELAY_DEFAULT_TARGET_URL", "https://example.test/default")
    monkeypatch.setenv("ALERT_RELAY_FAIL_ON_ERROR", "false")

    def _fake_post(url: str, json: dict, timeout: int):
        _ = url, json, timeout
        raise requests.ConnectionError("down")

    monkeypatch.setattr(relay.requests, "post", _fake_post)
    client = TestClient(relay.app)
    resp = client.post("/webhook/default", json={"alerts": []})
    assert resp.status_code == 200
    assert resp.json()["errors"] == 1


def test_webhook_retries_retryable_status_then_success(monkeypatch) -> None:
    monkeypatch.setenv("ALERT_RELAY_WARNING_TARGET_URL", "https://example.test/warn")
    monkeypatch.setenv("ALERT_RELAY_FAIL_ON_ERROR", "true")
    monkeypatch.setenv("ALERT_RELAY_RETRIES", "2")
    monkeypatch.setenv("ALERT_RELAY_RETRY_BACKOFF_MS", "0")
    monkeypatch.setenv("ALERT_RELAY_RETRY_STATUSES", "503")
    calls: list[str] = []

    def _fake_post(url: str, json: dict, timeout: int):
        _ = json, timeout
        calls.append(url)
        if len(calls) == 1:
            return _Resp(503)
        return _Resp(200)

    monkeypatch.setattr(relay.requests, "post", _fake_post)
    client = TestClient(relay.app)
    resp = client.post("/webhook/warning", json={"alerts": []})
    assert resp.status_code == 200
    assert resp.json()["forwarded"] == 1
    assert len(calls) == 2


def test_webhook_retries_connection_error_then_success(monkeypatch) -> None:
    monkeypatch.setenv("ALERT_RELAY_DEFAULT_TARGET_URL", "https://example.test/default")
    monkeypatch.setenv("ALERT_RELAY_FAIL_ON_ERROR", "true")
    monkeypatch.setenv("ALERT_RELAY_RETRIES", "1")
    monkeypatch.setenv("ALERT_RELAY_RETRY_BACKOFF_MS", "0")
    calls: list[str] = []

    def _fake_post(url: str, json: dict, timeout: int):
        _ = json, timeout
        calls.append(url)
        if len(calls) == 1:
            raise requests.ConnectionError("temporary")
        return _Resp(200)

    monkeypatch.setattr(relay.requests, "post", _fake_post)
    client = TestClient(relay.app)
    resp = client.post("/webhook/default", json={"alerts": []})
    assert resp.status_code == 200
    assert resp.json()["forwarded"] == 1
    assert len(calls) == 2


def test_webhook_non_retryable_status_fails_immediately(monkeypatch) -> None:
    monkeypatch.setenv("ALERT_RELAY_CRITICAL_TARGET_URL", "https://example.test/critical")
    monkeypatch.setenv("ALERT_RELAY_FAIL_ON_ERROR", "true")
    monkeypatch.setenv("ALERT_RELAY_RETRIES", "3")
    monkeypatch.setenv("ALERT_RELAY_RETRY_BACKOFF_MS", "0")
    monkeypatch.setenv("ALERT_RELAY_RETRY_STATUSES", "503")
    calls: list[str] = []

    def _fake_post(url: str, json: dict, timeout: int):
        _ = json, timeout
        calls.append(url)
        return _Resp(400)

    monkeypatch.setattr(relay.requests, "post", _fake_post)
    client = TestClient(relay.app)
    resp = client.post("/webhook/critical", json={"alerts": []})
    assert resp.status_code == 502
    assert len(calls) == 1
