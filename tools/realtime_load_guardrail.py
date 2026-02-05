"""
Realtime/post-meeting load guardrail for pipeline latency and error rate.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass
class MeetingRunResult:
    meeting_id: str
    ok: bool
    error: str
    start_latency_ms: float
    chunk_latencies_ms: list[float]
    e2e_latency_ms: float


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pipeline load guardrail")
    p.add_argument("--base-url", default="http://127.0.0.1:8010", help="API base URL")
    p.add_argument("--user-key", default="dev-user-key", help="X-API-Key for user endpoints")
    p.add_argument("--service-key", default="", help="X-API-Key for service admin checks")
    p.add_argument("--meetings", type=int, default=20, help="How many meetings to run")
    p.add_argument("--concurrency", type=int, default=6, help="Parallel meetings")
    p.add_argument("--chunks-per-meeting", type=int, default=3, help="Chunks in each meeting")
    p.add_argument("--report-timeout-sec", type=int, default=180, help="Timeout per meeting")
    p.add_argument("--poll-interval-sec", type=float, default=1.0, help="Polling interval")
    p.add_argument(
        "--chunk-b64",
        default=base64.b64encode(b"load-guardrail-chunk").decode("ascii"),
        help="Base64 audio chunk payload",
    )
    p.add_argument("--max-failure-rate", type=float, default=0.10, help="Guardrail for failures")
    p.add_argument("--max-p95-ingest-ms", type=float, default=700.0, help="Guardrail p95 ingest")
    p.add_argument("--max-p95-e2e-ms", type=float, default=60000.0, help="Guardrail p95 e2e")
    p.add_argument(
        "--min-throughput-meetings-per-min",
        type=float,
        default=4.0,
        help="Guardrail for throughput",
    )
    p.add_argument(
        "--max-total-dlq-depth",
        type=int,
        default=0,
        help="Guardrail for total DLQ depth (requires --service-key)",
    )
    p.add_argument(
        "--strict-admin-checks",
        action="store_true",
        help="Fail run if admin checks are unavailable",
    )
    p.add_argument(
        "--report-json",
        default="reports/realtime_load_guardrail.json",
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


def _post_json(
    url: str, *, payload: dict[str, Any], headers: dict[str, str], timeout: int
) -> requests.Response:
    return requests.post(url, json=payload, headers=headers, timeout=timeout)


def _run_meeting(
    *,
    base_url: str,
    user_key: str,
    meeting_id: str,
    chunk_b64: str,
    chunks_per_meeting: int,
    report_timeout_sec: int,
    poll_interval_sec: float,
) -> MeetingRunResult:
    user_headers = {"X-API-Key": user_key}
    start_payload = {
        "meeting_id": meeting_id,
        "mode": "postmeeting",
        "language": "ru",
        "consent": "unknown",
        "context": {"source": "load_guardrail"},
        "recipients": [],
    }
    start_t = time.perf_counter()
    start_resp = _post_json(
        f"{base_url}/v1/meetings/start", payload=start_payload, headers=user_headers, timeout=15
    )
    if start_resp.status_code >= 400:
        return MeetingRunResult(
            meeting_id=meeting_id,
            ok=False,
            error=f"start failed: {start_resp.status_code} {start_resp.text[:200]}",
            start_latency_ms=(time.perf_counter() - start_t) * 1000.0,
            chunk_latencies_ms=[],
            e2e_latency_ms=0.0,
        )
    start_latency_ms = (time.perf_counter() - start_t) * 1000.0

    chunk_latencies: list[float] = []
    first_ingest_t = 0.0
    for seq in range(1, chunks_per_meeting + 1):
        payload = {
            "seq": seq,
            "content_b64": chunk_b64,
            "codec": "pcm",
            "sample_rate": 16000,
            "channels": 1,
        }
        st = time.perf_counter()
        ingest = _post_json(
            f"{base_url}/v1/meetings/{meeting_id}/chunks",
            payload=payload,
            headers=user_headers,
            timeout=15,
        )
        if first_ingest_t <= 0.0:
            first_ingest_t = st
        chunk_latencies.append((time.perf_counter() - st) * 1000.0)
        if ingest.status_code >= 400:
            return MeetingRunResult(
                meeting_id=meeting_id,
                ok=False,
                error=f"chunk[{seq}] failed: {ingest.status_code} {ingest.text[:200]}",
                start_latency_ms=start_latency_ms,
                chunk_latencies_ms=chunk_latencies,
                e2e_latency_ms=0.0,
            )

    deadline = time.time() + report_timeout_sec
    while time.time() < deadline:
        get_resp = requests.get(
            f"{base_url}/v1/meetings/{meeting_id}", headers=user_headers, timeout=15
        )
        if get_resp.status_code >= 400:
            return MeetingRunResult(
                meeting_id=meeting_id,
                ok=False,
                error=f"get failed: {get_resp.status_code} {get_resp.text[:200]}",
                start_latency_ms=start_latency_ms,
                chunk_latencies_ms=chunk_latencies,
                e2e_latency_ms=0.0,
            )
        data = get_resp.json()
        if data.get("report") is not None and data.get("enhanced_transcript"):
            return MeetingRunResult(
                meeting_id=meeting_id,
                ok=True,
                error="",
                start_latency_ms=start_latency_ms,
                chunk_latencies_ms=chunk_latencies,
                e2e_latency_ms=(time.perf_counter() - first_ingest_t) * 1000.0,
            )
        time.sleep(poll_interval_sec)

    return MeetingRunResult(
        meeting_id=meeting_id,
        ok=False,
        error="report timeout",
        start_latency_ms=start_latency_ms,
        chunk_latencies_ms=chunk_latencies,
        e2e_latency_ms=0.0,
    )


def _load_total_dlq_depth(*, base_url: str, service_key: str) -> int:
    if not service_key:
        return -1
    resp = requests.get(
        f"{base_url}/v1/admin/queues/health",
        headers={"X-API-Key": service_key},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    queues = data.get("queues") or []
    total = 0
    for item in queues:
        try:
            total += int(item.get("dlq_depth") or 0)
        except Exception:
            continue
    return total


def main() -> int:
    args = _args()
    base_url = args.base_url.rstrip("/")
    if args.require_real_connector:
        provider = (os.getenv("MEETING_CONNECTOR_PROVIDER", "") or "").strip().lower()
        if provider != "sberjazz":
            print("require_real_connector_failed: MEETING_CONNECTOR_PROVIDER is not sberjazz")
            return 2

    started_at = time.time()
    run_id = int(started_at)
    results: list[MeetingRunResult] = []

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = [
            pool.submit(
                _run_meeting,
                base_url=base_url,
                user_key=args.user_key,
                meeting_id=f"load-{run_id}-{idx}",
                chunk_b64=args.chunk_b64,
                chunks_per_meeting=args.chunks_per_meeting,
                report_timeout_sec=args.report_timeout_sec,
                poll_interval_sec=args.poll_interval_sec,
            )
            for idx in range(args.meetings)
        ]
        for f in as_completed(futures):
            results.append(f.result())

    elapsed_sec = max(0.001, time.time() - started_at)
    total_meetings = len(results)
    successful = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    failure_rate = (len(failed) / total_meetings) if total_meetings else 1.0

    ingest_latencies = [v for r in results for v in r.chunk_latencies_ms]
    e2e_latencies = [r.e2e_latency_ms for r in successful if r.e2e_latency_ms > 0]

    p95_ingest = _percentile(ingest_latencies, 0.95)
    p95_e2e = _percentile(e2e_latencies, 0.95)
    throughput_mpm = (len(successful) * 60.0) / elapsed_sec

    dlq_total = -1
    dlq_error = ""
    if args.service_key:
        try:
            dlq_total = _load_total_dlq_depth(base_url=base_url, service_key=args.service_key)
        except Exception as e:
            dlq_error = str(e)

    checks = {
        "failure_rate": {
            "ok": failure_rate <= args.max_failure_rate,
            "actual": failure_rate,
            "threshold": args.max_failure_rate,
        },
        "p95_ingest_ms": {
            "ok": p95_ingest <= args.max_p95_ingest_ms,
            "actual": p95_ingest,
            "threshold": args.max_p95_ingest_ms,
        },
        "p95_e2e_ms": {
            "ok": p95_e2e <= args.max_p95_e2e_ms,
            "actual": p95_e2e,
            "threshold": args.max_p95_e2e_ms,
        },
        "throughput_meetings_per_min": {
            "ok": throughput_mpm >= args.min_throughput_meetings_per_min,
            "actual": throughput_mpm,
            "threshold": args.min_throughput_meetings_per_min,
        },
    }
    if args.service_key:
        checks["total_dlq_depth"] = {
            "ok": (dlq_total >= 0 and dlq_total <= args.max_total_dlq_depth)
            or (dlq_total < 0 and not args.strict_admin_checks),
            "actual": dlq_total,
            "threshold": args.max_total_dlq_depth,
            "error": dlq_error,
            "skipped": dlq_total < 0 and not args.strict_admin_checks,
        }

    report = {
        "scenario": {
            "base_url": base_url,
            "meetings": args.meetings,
            "concurrency": args.concurrency,
            "chunks_per_meeting": args.chunks_per_meeting,
            "report_timeout_sec": args.report_timeout_sec,
            "max_failure_rate": args.max_failure_rate,
            "max_p95_ingest_ms": args.max_p95_ingest_ms,
            "max_p95_e2e_ms": args.max_p95_e2e_ms,
            "min_throughput_meetings_per_min": args.min_throughput_meetings_per_min,
            "max_total_dlq_depth": args.max_total_dlq_depth,
        },
        "summary": {
            "elapsed_sec": elapsed_sec,
            "meetings_total": total_meetings,
            "meetings_success": len(successful),
            "meetings_failed": len(failed),
            "failure_rate": failure_rate,
            "throughput_meetings_per_min": throughput_mpm,
            "p95_ingest_ms": p95_ingest,
            "p95_e2e_ms": p95_e2e,
            "dlq_total": dlq_total,
        },
        "checks": checks,
        "failed_meetings": [
            {
                "meeting_id": r.meeting_id,
                "error": r.error,
            }
            for r in failed[:20]
        ],
        "results": [asdict(r) for r in results],
    }

    report_path = Path(args.report_json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    all_ok = all(bool(v.get("ok")) for v in checks.values())
    print(
        "load guardrail "
        + ("OK" if all_ok else "FAILED")
        + f": success={len(successful)}/{total_meetings}, "
        + f"failure_rate={failure_rate:.3f}, p95_ingest_ms={p95_ingest:.1f}, "
        + f"p95_e2e_ms={p95_e2e:.1f}, throughput_mpm={throughput_mpm:.2f}, dlq_total={dlq_total}"
    )
    print(f"report: {report_path}")
    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
