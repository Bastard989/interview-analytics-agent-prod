from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api_gateway.routers.meetings import router as meetings_router
from interview_analytics_agent.common.config import get_settings
from interview_analytics_agent.common.errors import ErrCode, ProviderError


@pytest.fixture()
def auth_settings():
    s = get_settings()
    keys = [
        "auth_mode",
        "api_keys",
        "service_api_keys",
        "security_audit_db_enabled",
        "meeting_auto_join_on_start",
        "meeting_connector_provider",
    ]
    snapshot = {k: getattr(s, k) for k in keys}
    try:
        s.security_audit_db_enabled = False
        s.auth_mode = "api_key"
        s.api_keys = "user-1"
        s.service_api_keys = "svc-1"
        yield s
    finally:
        for k, v in snapshot.items():
            setattr(s, k, v)


def _client(monkeypatch) -> TestClient:
    @contextmanager
    def _fake_db_session():
        yield object()

    class _FakeMeetingRepo:
        def __init__(self, _session):
            pass

        def save(self, _m):
            return None

    monkeypatch.setattr("apps.api_gateway.routers.meetings.db_session", _fake_db_session)
    monkeypatch.setattr("apps.api_gateway.routers.meetings.MeetingRepository", _FakeMeetingRepo)
    monkeypatch.setattr(
        "apps.api_gateway.routers.meetings.create_meeting",
        lambda meeting_id, context, consent: SimpleNamespace(
            id=meeting_id or "m-auto",
            status="queued",
            context=context,
            consent=consent,
        ),
    )

    app = FastAPI()
    app.include_router(meetings_router, prefix="/v1")
    return TestClient(app)


@dataclass
class _JoinState:
    provider: str
    connected: bool


def test_start_meeting_auto_join_by_request(monkeypatch, auth_settings) -> None:
    auth_settings.meeting_auto_join_on_start = False
    auth_settings.meeting_connector_provider = "sberjazz_mock"
    monkeypatch.setattr(
        "apps.api_gateway.routers.meetings.join_sberjazz_meeting",
        lambda meeting_id: _JoinState(provider="sberjazz_mock", connected=True),
    )
    client = _client(monkeypatch)

    resp = client.post(
        "/v1/meetings/start",
        headers={"X-API-Key": "user-1"},
        json={
            "meeting_id": "m-join-1",
            "mode": "realtime",
            "consent": "unknown",
            "context": {},
            "auto_join_connector": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meeting_id"] == "m-join-1"
    assert body["connector_auto_join"] is True
    assert body["connector_provider"] == "sberjazz_mock"
    assert body["connector_connected"] is True


def test_start_meeting_no_auto_join_for_postmeeting(monkeypatch, auth_settings) -> None:
    auth_settings.meeting_auto_join_on_start = True
    auth_settings.meeting_connector_provider = "sberjazz_mock"
    called = {"join": 0}

    def _join(_meeting_id: str):
        called["join"] += 1
        return _JoinState(provider="sberjazz_mock", connected=True)

    monkeypatch.setattr("apps.api_gateway.routers.meetings.join_sberjazz_meeting", _join)
    client = _client(monkeypatch)

    resp = client.post(
        "/v1/meetings/start",
        headers={"X-API-Key": "user-1"},
        json={
            "meeting_id": "m-no-join-1",
            "mode": "postmeeting",
            "consent": "unknown",
            "context": {},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["connector_auto_join"] is False
    assert body["connector_provider"] is None
    assert body["connector_connected"] is None
    assert called["join"] == 0


def test_start_meeting_auto_join_error_returns_503(monkeypatch, auth_settings) -> None:
    auth_settings.meeting_auto_join_on_start = True
    auth_settings.meeting_connector_provider = "sberjazz"

    def _fail_join(_meeting_id: str):
        raise ProviderError(
            ErrCode.CONNECTOR_PROVIDER_ERROR,
            "connector unavailable",
            details={"provider": "sberjazz"},
        )

    monkeypatch.setattr("apps.api_gateway.routers.meetings.join_sberjazz_meeting", _fail_join)
    client = _client(monkeypatch)

    resp = client.post(
        "/v1/meetings/start",
        headers={"X-API-Key": "user-1"},
        json={
            "meeting_id": "m-join-fail-1",
            "mode": "realtime",
            "consent": "unknown",
            "context": {},
        },
    )
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["code"] == ErrCode.CONNECTOR_PROVIDER_ERROR
