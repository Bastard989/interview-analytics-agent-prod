#!/usr/bin/env python3
"""One-command quick recorder for video meetings."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Quick recording for video meetings")
    p.add_argument("--url", required=True, help="Meeting URL (http/https)")
    p.add_argument("--output-dir", default=os.getenv("QUICK_RECORD_OUTPUT_DIR", "recordings"))
    p.add_argument(
        "--duration-sec",
        type=int,
        default=(int(os.getenv("QUICK_RECORD_DURATION_SEC", "0")) or None),
        help="Stop automatically after N seconds (default: wait for Enter)",
    )

    p.add_argument(
        "--segment-length-sec",
        type=int,
        default=int(os.getenv("QUICK_RECORD_SEGMENT_LENGTH_SEC", "120")),
    )
    p.add_argument(
        "--overlap-sec",
        type=int,
        default=int(os.getenv("QUICK_RECORD_OVERLAP_SEC", "30")),
    )
    p.add_argument("--sample-rate", type=int, default=int(os.getenv("QUICK_RECORD_SAMPLE_RATE", "44100")))
    p.add_argument("--block-size", type=int, default=int(os.getenv("QUICK_RECORD_BLOCK_SIZE", "1024")))
    p.add_argument("--no-open", action="store_true", help="Do not auto-open meeting URL")

    p.add_argument("--transcribe", action="store_true", help="Run local whisper transcription after recording")
    p.add_argument("--language", default=os.getenv("QUICK_RECORD_LANGUAGE", "ru"))
    p.add_argument("--whisper-model-size", default=os.getenv("QUICK_RECORD_WHISPER_MODEL_SIZE"))

    p.add_argument("--upload-to-agent", action="store_true", help="Upload mp3 into /v1 pipeline")
    p.add_argument("--agent-base-url", default=os.getenv("QUICK_RECORD_AGENT_BASE_URL", "http://127.0.0.1:8010"))
    p.add_argument("--agent-api-key", default=os.getenv("QUICK_RECORD_AGENT_API_KEY"))
    p.add_argument("--meeting-id", default=os.getenv("QUICK_RECORD_MEETING_ID"))
    p.add_argument("--wait-report-sec", type=int, default=int(os.getenv("QUICK_RECORD_WAIT_REPORT_SEC", "180")))
    p.add_argument("--poll-interval-sec", type=float, default=float(os.getenv("QUICK_RECORD_POLL_INTERVAL_SEC", "3")))

    p.add_argument(
        "--email-to",
        action="append",
        default=[],
        help="Recipient email (repeat flag or comma-separated list)",
    )

    return p.parse_args()


def _parse_recipients(raw_values: list[str]) -> list[str]:
    recipients: list[str] = []
    for value in raw_values:
        for part in value.split(","):
            email = part.strip()
            if email:
                recipients.append(email)
    return recipients


def main() -> int:
    from interview_analytics_agent.quick_record import QuickRecordConfig, run_quick_record

    args = _parse_args()

    recipients = _parse_recipients(args.email_to)

    cfg = QuickRecordConfig(
        meeting_url=args.url,
        output_dir=Path(args.output_dir),
        segment_length_sec=args.segment_length_sec,
        overlap_sec=args.overlap_sec,
        sample_rate=args.sample_rate,
        block_size=args.block_size,
        auto_open_url=not args.no_open,
        max_duration_sec=args.duration_sec,
        transcribe=args.transcribe,
        transcribe_language=args.language,
        whisper_model_size=args.whisper_model_size,
        upload_to_agent=args.upload_to_agent,
        agent_base_url=args.agent_base_url,
        agent_api_key=args.agent_api_key,
        meeting_id=args.meeting_id,
        wait_report_sec=args.wait_report_sec,
        poll_interval_sec=args.poll_interval_sec,
        email_to=recipients,
    )

    if cfg.upload_to_agent and not cfg.agent_api_key:
        print("error: --upload-to-agent requires --agent-api-key or QUICK_RECORD_AGENT_API_KEY", file=sys.stderr)
        return 2

    result = run_quick_record(cfg)

    print(f"MP3 saved: {result.mp3_path}")
    if result.transcript_path:
        print(f"Transcript saved: {result.transcript_path}")
    if result.agent_upload:
        print(
            f"Uploaded to agent: meeting_id={result.agent_upload.meeting_id}, "
            f"status={result.agent_upload.status}"
        )
    if result.email_result:
        print(f"Email delivery: ok={result.email_result.ok}, provider={result.email_result.provider}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
