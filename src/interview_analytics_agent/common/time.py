"""
Утилиты времени.

Назначение:
- единый формат времени (ISO UTC)
- миллисекунды для realtime таймстампов
"""

from __future__ import annotations

import datetime as dt

UTC = getattr(dt, "UTC", dt.timezone.utc)  # noqa: UP017


def utc_now() -> dt.datetime:
    """
    Текущее время в UTC (datetime).
    """
    return dt.datetime.now(UTC)


def utc_now_iso() -> str:
    """
    Текущее время в UTC в ISO формате.
    """
    return utc_now().isoformat()


def utc_ms() -> int:
    """
    Текущее время в UTC в миллисекундах (int).
    """
    return int(utc_now().timestamp() * 1000)
