"""
Smoke check for alert-relay fail-open/fail-closed policy under delivery failure.
"""

from __future__ import annotations

import argparse
import re
import time
from typing import Any

import requests


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Alert relay failure policy smoke")
    p.add_argument("--relay-url", default="http://localhost:9081", help="Alert relay base URL")
    p.add_argument("--expected-status", type=int, choices=[200, 502], required=True)
    p.add_argument(
        "--expect-fail-on-error",
        choices=["true", "false"],
        required=True,
        help="Expected ALERT_RELAY_FAIL_ON_ERROR on relay health endpoint",
    )
    p.add_argument("--timeout-sec", type=int, default=45, help="Wait timeout for relay readiness")
    return p.parse_args()


def _parse_labels(labels_raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for token in labels_raw.split(","):
        chunk = token.strip()
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        out[key.strip()] = value.strip().strip('"')
    return out


def _metric_sum(metrics_text: str, metric: str, *, labels: dict[str, str]) -> float:
    total = 0.0
    pattern = rf"^{re.escape(metric)}\{{([^}}]*)\}}\s+([0-9.eE+\-]+)$"
    for line in metrics_text.splitlines():
        match = re.match(pattern, line.strip())
        if not match:
            continue
        sample_labels = _parse_labels(match.group(1))
        if all(sample_labels.get(k) == v for k, v in labels.items()):
            total += float(match.group(2))
    return total


def _wait_health(*, relay_url: str, expected_fail_on_error: bool, timeout_sec: int) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            r = requests.get(f"{relay_url}/health", timeout=5)
            r.raise_for_status()
            data = r.json()
            if bool(data.get("fail_on_error")) is expected_fail_on_error:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("relay health did not reach expected fail_on_error mode in time")


def _load_metrics(*, relay_url: str) -> str:
    r = requests.get(f"{relay_url}/metrics", timeout=5)
    r.raise_for_status()
    return r.text


def _send_webhook(*, relay_url: str) -> requests.Response:
    payload: dict[str, Any] = {
        "alerts": [
            {
                "labels": {"alertname": "RelayFailurePolicySmoke", "severity": "critical"},
                "annotations": {"summary": "Relay failure policy smoke"},
            }
        ]
    }
    return requests.post(f"{relay_url}/webhook/critical", json=payload, timeout=15)


def main() -> int:
    args = _args()
    expected_fail_on_error = args.expect_fail_on_error == "true"
    metric_labels = {"channel": "critical", "target": "target"}

    try:
        _wait_health(
            relay_url=args.relay_url,
            expected_fail_on_error=expected_fail_on_error,
            timeout_sec=args.timeout_sec,
        )
        before = _load_metrics(relay_url=args.relay_url)
        before_errors = _metric_sum(
            before, "agent_alert_relay_forward_total", labels={**metric_labels, "result": "error"}
        )
        before_retries = _metric_sum(
            before, "agent_alert_relay_retries_total", labels=metric_labels
        )

        resp = _send_webhook(relay_url=args.relay_url)
        if resp.status_code != args.expected_status:
            raise RuntimeError(
                f"unexpected status: got={resp.status_code}, expected={args.expected_status}"
            )

        if args.expected_status == 200:
            data = resp.json()
            if int(data.get("errors", 0)) < 1:
                raise RuntimeError("expected errors>=1 for fail-open mode")

        after = _load_metrics(relay_url=args.relay_url)
        after_errors = _metric_sum(
            after, "agent_alert_relay_forward_total", labels={**metric_labels, "result": "error"}
        )
        after_retries = _metric_sum(after, "agent_alert_relay_retries_total", labels=metric_labels)

        if after_errors < before_errors + 1:
            raise RuntimeError(
                f"error counter did not grow (before={before_errors}, after={after_errors})"
            )
        if after_retries < before_retries + 1:
            raise RuntimeError(
                f"retry counter did not grow (before={before_retries}, after={after_retries})"
            )
    except Exception as e:
        print(f"alert relay failure policy smoke FAILED: {e}")
        return 2

    mode = "fail-closed" if expected_fail_on_error else "fail-open"
    print(
        "alert relay failure policy smoke OK: "
        f"mode={mode}, status={args.expected_status}, error+retry metrics increased"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
