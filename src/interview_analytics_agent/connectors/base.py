"""
Базовые интерфейсы коннекторов (интеграции с внешними системами).

Назначение:
- стандартизировать адаптеры (например, к платформе встреч/звонков)
- отделить "как подключаемся" от "что делаем дальше в пайплайне"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class MeetingContext:
    """
    Контекст встречи (минимальный runtime-объект).
    """

    meeting_id: str
    language: str = "ru"
    mode: str = "realtime"
    participants: list[dict[str, Any]] | None = None
    consent_required: bool = False
    delivery: dict[str, Any] | None = None


class MeetingConnector(Protocol):
    """
    Контракт коннектора встреч.
    """

    def join(self, meeting_id: str) -> MeetingContext:
        """Подключиться к встрече и вернуть контекст."""
        ...

    def leave(self, meeting_id: str) -> None:
        """Отключиться от встречи."""
        ...

    def fetch_recording(self, meeting_id: str) -> dict | None:
        """Получить запись/ссылку на запись после встречи (если есть)."""
        ...

    def fetch_live_chunks(
        self, meeting_id: str, *, cursor: str | None = None, limit: int = 20
    ) -> dict | None:
        """Получить очередную порцию live-чанков (best effort)."""
        ...
