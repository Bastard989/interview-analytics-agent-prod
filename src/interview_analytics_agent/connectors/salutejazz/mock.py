"""
Mock-коннектор SaluteJazz для dev/тестов.

Назначение:
- позволить гонять пайплайн без реальной платформы встреч
"""

from __future__ import annotations

from interview_analytics_agent.connectors.base import MeetingConnector, MeetingContext


class MockSaluteJazzConnector(MeetingConnector):
    def join(self, meeting_id: str) -> MeetingContext:
        return MeetingContext(
            meeting_id=meeting_id, participants=[{"name": "MockUser", "role": "candidate"}]
        )

    def leave(self, meeting_id: str) -> None:
        return None

    def fetch_recording(self, meeting_id: str):
        return {"type": "audio", "where": "s3://mock", "duration_sec": 0}

    def fetch_live_chunks(
        self, meeting_id: str, *, cursor: str | None = None, limit: int = 20
    ) -> dict | None:
        _ = meeting_id, cursor, limit
        return {"chunks": [], "next_cursor": cursor}
