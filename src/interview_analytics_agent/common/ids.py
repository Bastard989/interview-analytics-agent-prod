"""
Генерация идентификаторов.

Назначение:
- meeting_id / correlation_id / event_id
- idempotency_key для дедупликации
"""

from __future__ import annotations

import secrets
import uuid

from interview_analytics_agent.common.time import utc_now


def new_uuid() -> str:
    """UUIDv4 строкой."""
    return str(uuid.uuid4())


def new_event_id(prefix: str = "evt") -> str:
    """
    Идентификатор события (лог/очереди/трассировка).
    Формат: <prefix>_<UTCYYYYMMDDHHMMSS>_<rand>
    """
    ts = utc_now().strftime("%Y%m%d%H%M%S")
    rnd = secrets.token_hex(6)
    return f"{prefix}_{ts}_{rnd}"


def new_meeting_id(prefix: str = "mtg") -> str:
    """
    Идентификатор встречи.
    Формат: <prefix>_<UTCYYYYMMDDHHMMSS>_<rand>
    """
    ts = utc_now().strftime("%Y%m%d%H%M%S")
    rnd = secrets.token_hex(5)
    return f"{prefix}_{ts}_{rnd}"


def new_idempotency_key(prefix: str = "idem") -> str:
    """Ключ идемпотентности для дедупликации."""
    return f"{prefix}_{secrets.token_hex(16)}"


def new_correlation_id(prefix: str = "corr") -> str:
    """Correlation-id для сквозной трассировки."""
    return f"{prefix}_{secrets.token_hex(12)}"
