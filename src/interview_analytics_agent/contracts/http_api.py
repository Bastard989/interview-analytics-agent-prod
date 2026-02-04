"""
HTTP API контракты (Pydantic-модели).

Назначение:
- валидация входа/выхода на уровне FastAPI
- стабильные структуры для клиентов
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from interview_analytics_agent.domain.enums import ConsentStatus, MeetingMode

from .versions import HTTP_API_VERSION


# =============================================================================
# ЗАПРОСЫ
# =============================================================================
class MeetingStartRequest(BaseModel):
    api_version: str = Field(default=HTTP_API_VERSION)
    meeting_id: str | None = None

    mode: MeetingMode = Field(default=MeetingMode.realtime)
    language: str = Field(default="ru")

    # Согласие/ПДн
    consent: ConsentStatus = Field(default=ConsentStatus.unknown)

    # Участники/роли/контекст обработки
    context: dict[str, Any] = Field(default_factory=dict)
    auto_join_connector: bool | None = None

    # Куда доставлять результаты
    recipients: list[str] = Field(default_factory=list)


# =============================================================================
# ОТВЕТЫ
# =============================================================================
class MeetingStartResponse(BaseModel):
    api_version: str = Field(default=HTTP_API_VERSION)
    meeting_id: str
    status: str
    connector_auto_join: bool = False
    connector_provider: str | None = None
    connector_connected: bool | None = None


class MeetingGetResponse(BaseModel):
    api_version: str = Field(default=HTTP_API_VERSION)
    meeting_id: str
    status: str

    raw_transcript: str = ""
    enhanced_transcript: str = ""

    report: dict[str, Any] | None = None
