"""
Alert relay service.

Назначение:
- принимать webhooks от Alertmanager по severity channel
- форвардить во внешние каналы (Slack/PagerDuty/и т.д.)
- в dev/stage по умолчанию форвардить во внутренний alert-webhook-sink
"""

from __future__ import annotations

import os
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Request

app = FastAPI(title="Alert Relay", version="1.0.0")

_ALLOWED_CHANNELS = {"default", "warning", "critical"}


def _read_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _timeout_sec() -> int:
    try:
        return max(1, int(os.getenv("ALERT_RELAY_TIMEOUT_SEC", "5")))
    except Exception:
        return 5


def _fail_on_error() -> bool:
    return _read_bool("ALERT_RELAY_FAIL_ON_ERROR", True)


def _channel_name(raw: str) -> str:
    channel = (raw or "").strip().lower()
    return channel if channel in _ALLOWED_CHANNELS else "default"


def _target_url(channel: str) -> str:
    c = _channel_name(channel).upper()
    return (os.getenv(f"ALERT_RELAY_{c}_TARGET_URL", "") or "").strip()


def _shadow_url(channel: str) -> str:
    c = _channel_name(channel).upper()
    return (os.getenv(f"ALERT_RELAY_{c}_SHADOW_URL", "") or "").strip()


def _forward(*, url: str, payload: dict[str, Any]) -> None:
    resp = requests.post(url, json=payload, timeout=_timeout_sec())
    resp.raise_for_status()


@app.get("/health")
def health() -> dict[str, Any]:
    channels = {}
    for channel in sorted(_ALLOWED_CHANNELS):
        channels[channel] = {
            "target_set": bool(_target_url(channel)),
            "shadow_set": bool(_shadow_url(channel)),
        }
    return {
        "status": "ok",
        "fail_on_error": _fail_on_error(),
        "timeout_sec": _timeout_sec(),
        "channels": channels,
    }


@app.post("/webhook/{channel}")
async def webhook(channel: str, request: Request) -> dict[str, Any]:
    ch = _channel_name(channel)
    try:
        payload = await request.json()
    except Exception:
        payload = {"raw_body": (await request.body()).decode("utf-8", errors="replace")}
    if not isinstance(payload, dict):
        payload = {"value": payload}

    target = _target_url(ch)
    shadow = _shadow_url(ch)
    targets: list[tuple[str, str]] = []
    if target:
        targets.append(("target", target))
    if shadow and shadow != target:
        targets.append(("shadow", shadow))

    if not targets:
        # Без target/shadow webhook считаем обработанным (чтобы не блокировать Alertmanager).
        return {"status": "ok", "channel": ch, "forwarded": 0, "errors": 0}

    forwarded = 0
    errors: list[str] = []
    for kind, url in targets:
        try:
            _forward(url=url, payload=payload)
            forwarded += 1
        except Exception as e:
            errors.append(f"{kind}:{url}:{str(e)[:200]}")

    if errors and _fail_on_error():
        raise HTTPException(
            status_code=502,
            detail={
                "status": "error",
                "channel": ch,
                "forwarded": forwarded,
                "errors": errors,
            },
        )

    return {
        "status": "ok",
        "channel": ch,
        "forwarded": forwarded,
        "errors": len(errors),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9081)
