from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api_gateway.routers.realtime import router as realtime_router
from interview_analytics_agent.common.config import get_settings


@pytest.fixture()
def auth_settings():
    s = get_settings()
    keys = [
        "auth_mode",
        "api_keys",
        "service_api_keys",
        "security_audit_db_enabled",
    ]
    snapshot = {k: getattr(s, k) for k in keys}
    try:
        s.security_audit_db_enabled = False
        yield s
    finally:
        for k, v in snapshot.items():
            setattr(s, k, v)


def _payload(seq: int = 1) -> dict:
    return {
        "seq": seq,
        "content_b64": base64.b64encode(b"audio-bytes").decode("ascii"),
        "codec": "pcm",
        "sample_rate": 16000,
        "channels": 1,
    }


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(realtime_router, prefix="/v1")
    return TestClient(app)


def test_internal_chunks_requires_service_auth(monkeypatch, auth_settings) -> None:
    auth_settings.auth_mode = "api_key"
    auth_settings.api_keys = "user-1"
    auth_settings.service_api_keys = "svc-1"

    monkeypatch.setattr(
        "apps.api_gateway.routers.realtime.ingest_audio_chunk_b64",
        lambda **kwargs: type(
            "ChunkIngestResult",
            (),
            {
                "accepted": True,
                "meeting_id": kwargs["meeting_id"],
                "seq": kwargs["seq"],
                "idempotency_key": kwargs.get("idempotency_key") or "idem-1",
                "blob_key": f"meetings/{kwargs['meeting_id']}/chunks/{kwargs['seq']}.bin",
            },
        )(),
    )

    client = _client()
    denied = client.post(
        "/v1/internal/meetings/m-1/chunks",
        json=_payload(),
        headers={"X-API-Key": "user-1"},
    )
    assert denied.status_code == 403

    ok = client.post(
        "/v1/internal/meetings/m-1/chunks",
        json=_payload(),
        headers={"X-API-Key": "svc-1"},
    )
    assert ok.status_code == 200
    assert ok.json()["accepted"] is True


def test_public_chunks_allows_user_auth(monkeypatch, auth_settings) -> None:
    auth_settings.auth_mode = "api_key"
    auth_settings.api_keys = "user-1"
    auth_settings.service_api_keys = "svc-1"

    monkeypatch.setattr(
        "apps.api_gateway.routers.realtime.ingest_audio_chunk_b64",
        lambda **kwargs: type(
            "ChunkIngestResult",
            (),
            {
                "accepted": True,
                "meeting_id": kwargs["meeting_id"],
                "seq": kwargs["seq"],
                "idempotency_key": kwargs.get("idempotency_key") or "idem-2",
                "blob_key": f"meetings/{kwargs['meeting_id']}/chunks/{kwargs['seq']}.bin",
            },
        )(),
    )

    client = _client()
    resp = client.post("/v1/meetings/m-2/chunks", json=_payload(), headers={"X-API-Key": "user-1"})
    assert resp.status_code == 200
    assert resp.json()["meeting_id"] == "m-2"
