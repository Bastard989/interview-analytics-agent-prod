"""
Единые ошибки и коды ошибок.

Назначение:
- предсказуемые коды для HTTP/WS/очередей/DLQ
- единый стиль исключений по проекту
"""

from __future__ import annotations

from dataclasses import dataclass


class ErrCode:
    # Общие
    UNKNOWN = "unknown"
    VALIDATION = "validation"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"

    # Realtime / ingest
    BAD_FRAME = "bad_frame"
    SEQ_GAP = "seq_gap"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    BACKPRESSURE = "backpressure"

    # Провайдеры
    STT_PROVIDER_ERROR = "stt_provider_error"
    LLM_PROVIDER_ERROR = "llm_provider_error"
    DELIVERY_PROVIDER_ERROR = "delivery_provider_error"
    CONNECTOR_PROVIDER_ERROR = "connector_provider_error"
    CONNECTOR_AUTH_ERROR = "connector_auth_error"
    CONNECTOR_TIMEOUT = "connector_timeout"
    CONNECTOR_RATE_LIMIT = "connector_rate_limit"
    CONNECTOR_UNAVAILABLE = "connector_unavailable"
    CONNECTOR_BAD_REQUEST = "connector_bad_request"
    CONNECTOR_INVALID_RESPONSE = "connector_invalid_response"

    # Инфра/хранилища
    DB_ERROR = "db_error"
    REDIS_ERROR = "redis_error"
    STORAGE_ERROR = "storage_error"


@dataclass
class AppError(Exception):
    """
    Базовая ошибка приложения.
    - code: стабильный код ошибки
    - message: безопасное сообщение
    - details: доп. данные (без секретов/PII)
    """

    code: str
    message: str
    details: dict | None = None

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.code}: {self.message}"


class ValidationError(AppError):
    def __init__(self, message: str = "Ошибка валидации", details: dict | None = None) -> None:
        super().__init__(ErrCode.VALIDATION, message, details)


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Не авторизован", details: dict | None = None) -> None:
        super().__init__(ErrCode.UNAUTHORIZED, message, details)


class NotFoundError(AppError):
    def __init__(self, message: str = "Не найдено", details: dict | None = None) -> None:
        super().__init__(ErrCode.NOT_FOUND, message, details)


class ConflictError(AppError):
    def __init__(self, message: str = "Конфликт", details: dict | None = None) -> None:
        super().__init__(ErrCode.CONFLICT, message, details)


class ProviderError(AppError):
    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(code, message, details)
