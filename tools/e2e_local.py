"""
E2E smoke for interview-analytics-agent.

Contract:
- POST /v1/meetings/start
- POST /v1/meetings/{id}/chunks
- GET /v1/meetings/{id} -> enhanced_transcript/report
"""

from __future__ import annotations

import base64
import os
import socket
import subprocess
import sys
import time

import requests


def wait_tcp(host: str, port: int, timeout_s: int = 60) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError:
            time.sleep(1)
    raise RuntimeError(f"timeout waiting for {host}:{port}")


def wait_health(base_url: str, timeout_s: int = 90) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = requests.get(f"{base_url}/health", timeout=2)
            if r.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError("timeout waiting for /health")


def run_http_smoke(base_url: str) -> None:
    meeting_id = f"e2e-{int(time.time())}"
    start_payload = {
        "meeting_id": meeting_id,
        "mode": "postmeeting",
        "language": "ru",
        "consent": "unknown",
        "context": {"source": "e2e"},
        "recipients": [],
    }

    r = requests.post(f"{base_url}/v1/meetings/start", json=start_payload, timeout=10)
    r.raise_for_status()

    chunk_bytes = b"e2e-audio-chunk"
    chunk_payload = {
        "seq": 1,
        "content_b64": base64.b64encode(chunk_bytes).decode("ascii"),
        "codec": "pcm",
        "sample_rate": 16000,
        "channels": 1,
    }
    r = requests.post(
        f"{base_url}/v1/meetings/{meeting_id}/chunks",
        json=chunk_payload,
        timeout=10,
    )
    r.raise_for_status()

    deadline = time.time() + 60
    while time.time() < deadline:
        r = requests.get(f"{base_url}/v1/meetings/{meeting_id}", timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("enhanced_transcript") and data.get("report") is not None:
            return
        time.sleep(1.5)

    raise RuntimeError("meeting report was not produced in time")


def main() -> int:
    in_ci = os.environ.get("CI", "").lower() == "true"
    base_url = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8010").rstrip("/")

    if not in_ci:
        env = dict(os.environ)
        env.update(
            {
                "AUTH_MODE": env.get("AUTH_MODE", "none"),
                "STT_PROVIDER": env.get("STT_PROVIDER", "mock"),
                "LLM_ENABLED": env.get("LLM_ENABLED", "false"),
            }
        )
        try:
            subprocess.check_call(["docker", "compose", "up", "-d", "--build"], env=env)
        except Exception as e:
            print(f"compose up failed: {e}")
            return 2

        try:
            wait_tcp("127.0.0.1", 8010, timeout_s=90)
        except Exception as e:
            print(f"api not reachable: {e}")
            return 3

    try:
        wait_health(base_url)
        run_http_smoke(base_url)
    except Exception as e:
        print(f"e2e http smoke failed: {e}")
        return 4

    try:
        subprocess.check_call([sys.executable, "-m", "ruff", "check", "."])
        subprocess.check_call([sys.executable, "-m", "ruff", "format", ".", "--check"])
        subprocess.check_call([sys.executable, "-m", "pytest", "tests/unit", "-q"])
    except subprocess.CalledProcessError as e:
        return e.returncode

    print("e2e smoke OK (start -> ingest chunk -> report)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
