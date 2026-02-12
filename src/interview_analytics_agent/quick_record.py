"""
Quick recorder for video meetings.

Combines script-like usability (one-command recording) with production agent features:
- segmented loopback recording with overlap
- mp3 conversion and optional local whisper transcription
- optional upload into Interview Analytics Agent pipeline
- optional summary email via existing SMTP delivery provider
"""

from __future__ import annotations

import base64
import subprocess
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from interview_analytics_agent.common.logging import get_project_logger
from interview_analytics_agent.delivery.base import DeliveryResult
from interview_analytics_agent.delivery.email.sender import SMTPEmailProvider

log = get_project_logger()


@dataclass
class QuickRecordConfig:
    meeting_url: str
    output_dir: Path = Path("recordings")
    segment_length_sec: int = 120
    overlap_sec: int = 30
    sample_rate: int = 44100
    block_size: int = 1024
    auto_open_url: bool = True
    max_duration_sec: int | None = None

    transcribe: bool = False
    transcribe_language: str = "ru"
    whisper_model_size: str | None = None

    upload_to_agent: bool = False
    agent_base_url: str = "http://127.0.0.1:8010"
    agent_api_key: str | None = None
    meeting_id: str | None = None
    wait_report_sec: int = 180
    poll_interval_sec: float = 3.0

    email_to: list[str] | None = None


@dataclass
class AgentUploadResult:
    meeting_id: str
    status: str
    report: dict[str, Any] | None
    enhanced_transcript: str


@dataclass
class QuickRecordResult:
    mp3_path: Path
    transcript_path: Path | None
    agent_upload: AgentUploadResult | None
    email_result: DeliveryResult | None


def segment_step_seconds(segment_length_sec: int, overlap_sec: int) -> int:
    if segment_length_sec <= 0:
        raise ValueError("segment_length_sec must be > 0")
    if overlap_sec < 0:
        raise ValueError("overlap_sec must be >= 0")
    if overlap_sec >= segment_length_sec:
        raise ValueError("overlap_sec must be < segment_length_sec")
    return segment_length_sec - overlap_sec


def normalize_agent_base_url(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        raise ValueError("agent base url is empty")
    if normalized.endswith("/v1"):
        return normalized[: -len("/v1")]
    return normalized


def build_start_payload(*, meeting_id: str, meeting_url: str, language: str) -> dict[str, Any]:
    return {
        "meeting_id": meeting_id,
        "mode": "postmeeting",
        "language": language,
        "consent": "accepted",
        "context": {
            "source": "quick_record",
            "meeting_url": meeting_url,
        },
    }


def build_chunk_payload(
    *,
    audio_bytes: bytes,
    seq: int = 1,
    codec: str = "mp3",
    sample_rate: int = 44100,
    channels: int = 2,
) -> dict[str, Any]:
    return {
        "seq": seq,
        "content_b64": base64.b64encode(audio_bytes).decode("ascii"),
        "codec": codec,
        "sample_rate": sample_rate,
        "channels": channels,
    }


class SegmentedLoopbackRecorder:
    def __init__(
        self,
        *,
        base_path: Path,
        sample_rate: int,
        block_size: int,
        segment_length_sec: int,
        overlap_sec: int,
    ) -> None:
        self.base_path = base_path
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.segment_length_sec = segment_length_sec
        self.overlap_sec = overlap_sec
        self.step_sec = segment_step_seconds(segment_length_sec, overlap_sec)
        self.stop_event = threading.Event()
        self.segment_paths: list[Path] = []
        self._active: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def _select_loopback(self, sc_module: Any):
        mics = list(sc_module.all_microphones())
        loopback = next((m for m in mics if getattr(m, "is_loopback", False)), None)
        if loopback is None:
            loopback = sc_module.default_microphone()
        if loopback is None:
            raise RuntimeError("No microphone/loopback device available")
        return loopback

    def stop(self) -> None:
        self.stop_event.set()

    def record(self) -> None:
        try:
            try:
                import soundcard as sc
                import soundfile as sf
            except ImportError as exc:
                raise RuntimeError(
                    "quick recorder requires soundcard + soundfile (pip install -r requirements.txt)"
                ) from exc

            loopback = self._select_loopback(sc)
            next_start = 0.0
            index = 0
            started_at = time.monotonic()

            with loopback.recorder(samplerate=self.sample_rate, blocksize=self.block_size) as recorder:
                channels = len(recorder.channelmap)

                while not self.stop_event.is_set():
                    data = recorder.record(numframes=self.block_size)
                    elapsed = time.monotonic() - started_at

                    if elapsed >= next_start:
                        index += 1
                        seg_path = Path(f"{self.base_path}_{index:04d}.wav")
                        handle = sf.SoundFile(
                            str(seg_path),
                            mode="w",
                            samplerate=self.sample_rate,
                            channels=channels,
                        )
                        self._active.append({"start": elapsed, "file": handle})
                        self.segment_paths.append(seg_path)
                        next_start = elapsed + self.step_sec

                    for seg in list(self._active):
                        seg["file"].write(data)
                        if elapsed - float(seg["start"]) >= self.segment_length_sec:
                            seg["file"].close()
                            self._active.remove(seg)
        except Exception as exc:
            self.error = exc
        finally:
            for seg in self._active:
                seg["file"].close()
            self._active.clear()


def merge_segments_to_wav(
    *,
    segment_paths: list[Path],
    output_wav: Path,
    block_size: int = 4096,
    remove_sources: bool = True,
) -> None:
    if not segment_paths:
        raise RuntimeError("No recorded segments found")

    try:
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError(
            "Merging segments requires soundfile (pip install -r requirements.txt)"
        ) from exc

    ordered = sorted(segment_paths)
    with sf.SoundFile(str(ordered[0]), mode="r") as first:
        samplerate = int(first.samplerate)
        channels = int(first.channels)

    with sf.SoundFile(str(output_wav), mode="w", samplerate=samplerate, channels=channels) as out:
        for seg_path in ordered:
            with sf.SoundFile(str(seg_path), mode="r") as seg:
                for block in seg.blocks(blocksize=block_size):
                    out.write(block)
            if remove_sources:
                seg_path.unlink(missing_ok=True)


def convert_wav_to_mp3(*, wav_path: Path, mp3_path: Path) -> None:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(wav_path),
        "-codec:a",
        "libmp3lame",
        str(mp3_path),
    ]
    subprocess.run(cmd, check=True)


def transcribe_with_local_whisper(
    *,
    audio_path: Path,
    language: str,
    model_size: str | None = None,
) -> str:
    from interview_analytics_agent.stt.whisper_local import WhisperLocalProvider

    provider = WhisperLocalProvider(model_size=model_size, language=language)
    audio_bytes = audio_path.read_bytes()
    result = provider.transcribe_chunk(audio=audio_bytes, sample_rate=16000)
    return result.text.strip()


def upload_recording_to_agent(*, recording_path: Path, cfg: QuickRecordConfig) -> AgentUploadResult:
    if not cfg.agent_api_key:
        raise ValueError("agent_api_key is required for upload")

    meeting_id = cfg.meeting_id or f"quick-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    base_url = normalize_agent_base_url(cfg.agent_base_url)
    headers = {
        "X-API-Key": cfg.agent_api_key,
        "Content-Type": "application/json",
    }

    start_payload = build_start_payload(
        meeting_id=meeting_id,
        meeting_url=cfg.meeting_url,
        language=cfg.transcribe_language,
    )
    start_resp = requests.post(
        f"{base_url}/v1/meetings/start",
        json=start_payload,
        headers=headers,
        timeout=20,
    )
    start_resp.raise_for_status()

    chunk_payload = build_chunk_payload(
        audio_bytes=recording_path.read_bytes(),
        seq=1,
        codec="mp3",
        sample_rate=cfg.sample_rate,
        channels=2,
    )
    chunk_resp = requests.post(
        f"{base_url}/v1/meetings/{meeting_id}/chunks",
        json=chunk_payload,
        headers=headers,
        timeout=60,
    )
    chunk_resp.raise_for_status()

    deadline = time.monotonic() + max(1, int(cfg.wait_report_sec))
    last_status = "in_progress"
    last_report: dict[str, Any] | None = None
    last_transcript = ""

    while time.monotonic() < deadline:
        get_resp = requests.get(
            f"{base_url}/v1/meetings/{meeting_id}",
            headers=headers,
            timeout=20,
        )
        get_resp.raise_for_status()
        payload = get_resp.json()
        last_status = str(payload.get("status") or "unknown")
        last_report = payload.get("report")
        last_transcript = str(payload.get("enhanced_transcript") or "")

        if last_report or last_transcript:
            break

        time.sleep(max(0.1, float(cfg.poll_interval_sec)))

    return AgentUploadResult(
        meeting_id=meeting_id,
        status=last_status,
        report=last_report,
        enhanced_transcript=last_transcript,
    )


def send_summary_email(
    *,
    cfg: QuickRecordConfig,
    mp3_path: Path,
    transcript_path: Path | None,
    upload_result: AgentUploadResult | None,
) -> DeliveryResult | None:
    recipients = [r.strip() for r in (cfg.email_to or []) if r.strip()]
    if not recipients:
        return None

    meeting_id = upload_result.meeting_id if upload_result else (cfg.meeting_id or "quick-record")

    attachments: list[tuple[str, bytes, str]] = [
        (mp3_path.name, mp3_path.read_bytes(), "audio/mpeg"),
    ]
    if transcript_path and transcript_path.exists():
        attachments.append((transcript_path.name, transcript_path.read_bytes(), "text/plain"))

    text_body = (
        f"Recording finished.\n"
        f"Meeting URL: {cfg.meeting_url}\n"
        f"MP3: {mp3_path}\n"
        f"Transcript: {transcript_path or 'not generated'}\n"
        f"Agent meeting id: {meeting_id}\n"
    )

    html_body = (
        "<h3>Quick meeting recording finished</h3>"
        f"<p><b>Meeting URL:</b> {cfg.meeting_url}</p>"
        f"<p><b>MP3:</b> {mp3_path.name}</p>"
        f"<p><b>Transcript:</b> {transcript_path.name if transcript_path else 'not generated'}</p>"
        f"<p><b>Agent meeting id:</b> {meeting_id}</p>"
    )

    provider = SMTPEmailProvider()
    return provider.send_report(
        meeting_id=meeting_id,
        recipients=recipients,
        subject=f"Quick recording finished: {meeting_id}",
        html_body=html_body,
        text_body=text_body,
        attachments=attachments,
    )


def _validate_meeting_url(url: str) -> str:
    normalized = (url or "").strip()
    if not normalized:
        raise ValueError("meeting_url is empty")
    if not (normalized.startswith("http://") or normalized.startswith("https://")):
        raise ValueError("meeting_url must start with http:// or https://")
    return normalized


def run_quick_record(cfg: QuickRecordConfig) -> QuickRecordResult:
    cfg.meeting_url = _validate_meeting_url(cfg.meeting_url)
    segment_step_seconds(cfg.segment_length_sec, cfg.overlap_sec)

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"meeting_{timestamp}"

    seg_base = cfg.output_dir / base_name
    wav_path = cfg.output_dir / f"{base_name}.wav"
    mp3_path = cfg.output_dir / f"{base_name}.mp3"
    txt_path = cfg.output_dir / f"{base_name}.txt"

    if cfg.auto_open_url:
        webbrowser.open(cfg.meeting_url, new=2)

    recorder = SegmentedLoopbackRecorder(
        base_path=seg_base,
        sample_rate=cfg.sample_rate,
        block_size=cfg.block_size,
        segment_length_sec=cfg.segment_length_sec,
        overlap_sec=cfg.overlap_sec,
    )

    thread = threading.Thread(target=recorder.record, daemon=True)
    thread.start()

    if cfg.max_duration_sec and cfg.max_duration_sec > 0:
        time.sleep(cfg.max_duration_sec)
    else:
        try:
            input("Recording started. Press Enter to stop...\n")
        except EOFError:
            # non-interactive shell fallback
            time.sleep(5)

    recorder.stop()
    thread.join(timeout=20)
    if recorder.error is not None:
        raise RuntimeError(f"Recording failed: {recorder.error}") from recorder.error
    if not recorder.segment_paths:
        raise RuntimeError("Recording failed: no audio segments were produced")

    merge_segments_to_wav(segment_paths=recorder.segment_paths, output_wav=wav_path)
    convert_wav_to_mp3(wav_path=wav_path, mp3_path=mp3_path)
    wav_path.unlink(missing_ok=True)

    transcript_path: Path | None = None
    if cfg.transcribe:
        text = transcribe_with_local_whisper(
            audio_path=mp3_path,
            language=cfg.transcribe_language,
            model_size=cfg.whisper_model_size,
        )
        txt_path.write_text(text, encoding="utf-8")
        transcript_path = txt_path

    upload_result: AgentUploadResult | None = None
    if cfg.upload_to_agent:
        upload_result = upload_recording_to_agent(recording_path=mp3_path, cfg=cfg)

    email_result = send_summary_email(
        cfg=cfg,
        mp3_path=mp3_path,
        transcript_path=transcript_path,
        upload_result=upload_result,
    )

    log.info(
        "quick_record_done",
        extra={
            "payload": {
                "mp3_path": str(mp3_path),
                "transcript_path": str(transcript_path) if transcript_path else None,
                "uploaded": bool(upload_result),
                "email_sent": bool(email_result and email_result.ok),
            }
        },
    )

    return QuickRecordResult(
        mp3_path=mp3_path,
        transcript_path=transcript_path,
        agent_upload=upload_result,
        email_result=email_result,
    )
