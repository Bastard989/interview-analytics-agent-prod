"""
WS contours guardrail for /v1/ws (user) and /v1/ws/internal (service).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

try:
    import websockets
except Exception as e:  # pragma: no cover
    raise SystemExit(f"websockets library is required: {e}") from e


@dataclass
class WsMeetingResult:
    contour: str
    meeting_id: str
    ok: bool
    error: str
    ws_send_latencies_ms: list[float]
    e2e_latency_ms: float


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="WS contours load/auth guardrail")
    p.add_argument("--base-url", default="http://127.0.0.1:8010", help="HTTP API base URL")
    p.add_argument("--ws-base-url", default="", help="WS base URL, e.g. ws://127.0.0.1:8010")
    p.add_argument("--user-key", default="dev-user-key", help="User API key")
    p.add_argument("--service-key", default="dev-service-key", help="Service API key")
    p.add_argument("--meetings-per-contour", type=int, default=6, help="Meetings per contour")
    p.add_argument("--concurrency", type=int, default=4, help="Parallel tasks")
    p.add_argument("--chunks-per-meeting", type=int, default=2, help="Chunks in one WS session")
    p.add_argument("--report-timeout-sec", type=int, default=120, help="Report timeout")
    p.add_argument("--poll-interval-sec", type=float, default=1.0, help="Meeting polling interval")
    p.add_argument(
        "--chunk-b64",
        default=base64.b64encode(b"ws-guardrail-chunk").decode("ascii"),
        help="Base64 audio chunk payload",
    )
    p.add_argument("--max-failure-rate", type=float, default=0.10, help="Guardrail for failures")
    p.add_argument("--max-p95-ws-send-ms", type=float, default=120.0, help="Guardrail p95 ws send")
    p.add_argument("--max-p95-e2e-ms", type=float, default=60000.0, help="Guardrail p95 e2e")
    p.add_argument(
        "--strict-split-check",
        action="store_true",
        help="Fail if split-auth deny checks cannot be validated",
    )
    p.add_argument(
        "--report-json",
        default="reports/ws_contours_guardrail.json",
        help="Path to JSON report",
    )
    p.add_argument(
        "--require-real-connector",
        action="store_true",
        help="Require MEETING_CONNECTOR_PROVIDER=sberjazz when running guardrail.",
    )
    return p.parse_args()


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[low]
    frac = pos - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac


def _http_to_ws_base(http_base: str) -> str:
    h = http_base.rstrip("/")
    if h.startswith("https://"):
        return "wss://" + h[len("https://") :]
    if h.startswith("http://"):
        return "ws://" + h[len("http://") :]
    raise ValueError(f"unsupported base url: {http_base}")


def _start_meeting(*, base_url: str, user_key: str, meeting_id: str) -> None:
    payload = {
        "meeting_id": meeting_id,
        "mode": "postmeeting",
        "language": "ru",
        "consent": "unknown",
        "context": {"source": "ws_guardrail"},
        "recipients": [],
    }
    r = requests.post(
        f"{base_url}/v1/meetings/start",
        json=payload,
        headers={"X-API-Key": user_key},
        timeout=15,
    )
    r.raise_for_status()


def _wait_report(
    *,
    base_url: str,
    user_key: str,
    meeting_id: str,
    timeout_sec: int,
    poll_interval_sec: float,
) -> float:
    start = time.perf_counter()
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        r = requests.get(
            f"{base_url}/v1/meetings/{meeting_id}",
            headers={"X-API-Key": user_key},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("report") is not None and data.get("enhanced_transcript"):
            return (time.perf_counter() - start) * 1000.0
        time.sleep(poll_interval_sec)
    raise TimeoutError("report timeout")


async def _ws_connect(url: str, headers: dict[str, str]):
    try:
        return await websockets.connect(url, additional_headers=headers)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers)


async def _expect_denied(url: str, headers: dict[str, str]) -> bool:
    try:
        conn = await _ws_connect(url, headers)
        await conn.close()
        return False
    except Exception:
        return True


async def _run_one_meeting(
    *,
    contour: str,
    base_url: str,
    ws_base_url: str,
    user_key: str,
    service_key: str,
    meeting_id: str,
    chunks_per_meeting: int,
    chunk_b64: str,
    report_timeout_sec: int,
    poll_interval_sec: float,
) -> WsMeetingResult:
    try:
        await asyncio.to_thread(
            _start_meeting, base_url=base_url, user_key=user_key, meeting_id=meeting_id
        )
    except Exception as e:
        return WsMeetingResult(
            contour=contour,
            meeting_id=meeting_id,
            ok=False,
            error=f"start failed: {e}",
            ws_send_latencies_ms=[],
            e2e_latency_ms=0.0,
        )

    ws_path = "/v1/ws" if contour == "user" else "/v1/ws/internal"
    api_key = user_key if contour == "user" else service_key
    headers = {"X-API-Key": api_key}
    ws_send_latencies: list[float] = []

    try:
        conn = await _ws_connect(ws_base_url + ws_path, headers=headers)
    except Exception as e:
        return WsMeetingResult(
            contour=contour,
            meeting_id=meeting_id,
            ok=False,
            error=f"ws connect failed: {e}",
            ws_send_latencies_ms=[],
            e2e_latency_ms=0.0,
        )

    try:
        for seq in range(1, chunks_per_meeting + 1):
            event = {
                "event_type": "audio.chunk",
                "meeting_id": meeting_id,
                "seq": seq,
                "content_b64": chunk_b64,
                "codec": "pcm",
                "sample_rate": 16000,
                "channels": 1,
            }
            start_send = time.perf_counter()
            await conn.send(json.dumps(event))
            ws_send_latencies.append((time.perf_counter() - start_send) * 1000.0)
    except Exception as e:
        await conn.close()
        return WsMeetingResult(
            contour=contour,
            meeting_id=meeting_id,
            ok=False,
            error=f"ws send failed: {e}",
            ws_send_latencies_ms=ws_send_latencies,
            e2e_latency_ms=0.0,
        )

    await conn.close()

    try:
        e2e_latency = await asyncio.to_thread(
            _wait_report,
            base_url=base_url,
            user_key=user_key,
            meeting_id=meeting_id,
            timeout_sec=report_timeout_sec,
            poll_interval_sec=poll_interval_sec,
        )
    except Exception as e:
        return WsMeetingResult(
            contour=contour,
            meeting_id=meeting_id,
            ok=False,
            error=f"report failed: {e}",
            ws_send_latencies_ms=ws_send_latencies,
            e2e_latency_ms=0.0,
        )

    return WsMeetingResult(
        contour=contour,
        meeting_id=meeting_id,
        ok=True,
        error="",
        ws_send_latencies_ms=ws_send_latencies,
        e2e_latency_ms=e2e_latency,
    )


async def _run_load(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    if args.require_real_connector:
        provider = (os.getenv("MEETING_CONNECTOR_PROVIDER", "") or "").strip().lower()
        if provider != "sberjazz":
            raise RuntimeError(
                "require_real_connector_failed: MEETING_CONNECTOR_PROVIDER is not sberjazz"
            )
    ws_base_url = (args.ws_base_url or _http_to_ws_base(base_url)).rstrip("/")
    run_id = int(time.time())

    # auth split checks
    deny_user_with_service = await _expect_denied(
        ws_base_url + "/v1/ws", headers={"X-API-Key": args.service_key}
    )
    deny_internal_with_user = await _expect_denied(
        ws_base_url + "/v1/ws/internal", headers={"X-API-Key": args.user_key}
    )
    split_ok = deny_user_with_service and deny_internal_with_user

    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    results: list[WsMeetingResult] = []

    async def run_limited(contour: str, idx: int) -> None:
        async with semaphore:
            res = await _run_one_meeting(
                contour=contour,
                base_url=base_url,
                ws_base_url=ws_base_url,
                user_key=args.user_key,
                service_key=args.service_key,
                meeting_id=f"ws-{contour}-{run_id}-{idx}",
                chunks_per_meeting=args.chunks_per_meeting,
                chunk_b64=args.chunk_b64,
                report_timeout_sec=args.report_timeout_sec,
                poll_interval_sec=args.poll_interval_sec,
            )
            results.append(res)

    tasks = []
    for contour in ("user", "internal"):
        for idx in range(args.meetings_per_contour):
            tasks.append(asyncio.create_task(run_limited(contour, idx)))
    await asyncio.gather(*tasks)

    ws_send_latencies = [v for r in results for v in r.ws_send_latencies_ms]
    e2e_latencies = [r.e2e_latency_ms for r in results if r.ok and r.e2e_latency_ms > 0]
    failed = [r for r in results if not r.ok]
    failure_rate = (len(failed) / len(results)) if results else 1.0

    p95_ws_send = _percentile(ws_send_latencies, 0.95)
    p95_e2e = _percentile(e2e_latencies, 0.95)

    checks = {
        "auth_split": {
            "ok": split_ok or not args.strict_split_check,
            "deny_user_with_service": deny_user_with_service,
            "deny_internal_with_user": deny_internal_with_user,
            "strict": args.strict_split_check,
            "skipped": (not split_ok) and (not args.strict_split_check),
        },
        "failure_rate": {
            "ok": failure_rate <= args.max_failure_rate,
            "actual": failure_rate,
            "threshold": args.max_failure_rate,
        },
        "p95_ws_send_ms": {
            "ok": p95_ws_send <= args.max_p95_ws_send_ms,
            "actual": p95_ws_send,
            "threshold": args.max_p95_ws_send_ms,
        },
        "p95_e2e_ms": {
            "ok": p95_e2e <= args.max_p95_e2e_ms,
            "actual": p95_e2e,
            "threshold": args.max_p95_e2e_ms,
        },
    }

    return {
        "scenario": {
            "base_url": base_url,
            "ws_base_url": ws_base_url,
            "meetings_per_contour": args.meetings_per_contour,
            "concurrency": args.concurrency,
            "chunks_per_meeting": args.chunks_per_meeting,
            "report_timeout_sec": args.report_timeout_sec,
            "max_failure_rate": args.max_failure_rate,
            "max_p95_ws_send_ms": args.max_p95_ws_send_ms,
            "max_p95_e2e_ms": args.max_p95_e2e_ms,
            "strict_split_check": args.strict_split_check,
        },
        "summary": {
            "runs_total": len(results),
            "runs_failed": len(failed),
            "failure_rate": failure_rate,
            "p95_ws_send_ms": p95_ws_send,
            "p95_e2e_ms": p95_e2e,
        },
        "checks": checks,
        "failed_runs": [
            {"contour": r.contour, "meeting_id": r.meeting_id, "error": r.error}
            for r in failed[:20]
        ],
        "results": [asdict(r) for r in results],
    }


def main() -> int:
    args = _args()
    report = asyncio.run(_run_load(args))
    report_path = Path(args.report_json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    checks = report.get("checks", {})
    all_ok = all(bool(v.get("ok")) for v in checks.values())
    summary = report.get("summary", {})
    print(
        "ws contours guardrail "
        + ("OK" if all_ok else "FAILED")
        + f": failed={summary.get('runs_failed', 0)}/{summary.get('runs_total', 0)}, "
        + f"failure_rate={summary.get('failure_rate', 1.0):.3f}, "
        + f"p95_ws_send_ms={summary.get('p95_ws_send_ms', 0.0):.1f}, "
        + f"p95_e2e_ms={summary.get('p95_e2e_ms', 0.0):.1f}"
    )
    print(f"report: {report_path}")
    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
