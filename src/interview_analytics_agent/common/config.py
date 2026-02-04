"""
Централизованная конфигурация проекта (ENV / .env).

Важно:
- настройки читаются из .env и переменных окружения
- типизированные значения через pydantic-settings
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Runtime
    # -------------------------------------------------------------------------
    app_env: str = Field(default="dev", alias="APP_ENV")
    service_name: str = Field(default="api-gateway", alias="SERVICE_NAME")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8010, alias="API_PORT")
    cors_allowed_origins: str = Field(default="*", alias="CORS_ALLOWED_ORIGINS")
    cors_allow_credentials: bool = Field(default=True, alias="CORS_ALLOW_CREDENTIALS")

    # -------------------------------------------------------------------------
    # Auth
    # -------------------------------------------------------------------------
    auth_mode: str = Field(default="api_key", alias="AUTH_MODE")  # api_key|jwt|none
    api_keys: str = Field(default="", alias="API_KEYS")
    service_api_keys: str = Field(default="", alias="SERVICE_API_KEYS")
    allow_service_api_key_in_jwt_mode: bool = Field(
        default=True, alias="ALLOW_SERVICE_API_KEY_IN_JWT_MODE"
    )
    oidc_issuer_url: str | None = Field(default=None, alias="OIDC_ISSUER_URL")
    oidc_jwks_url: str | None = Field(default=None, alias="OIDC_JWKS_URL")
    oidc_audience: str | None = Field(default=None, alias="OIDC_AUDIENCE")
    oidc_algorithms: str = Field(default="RS256", alias="OIDC_ALGORITHMS")
    oidc_discovery_timeout_sec: int = Field(default=5, alias="OIDC_DISCOVERY_TIMEOUT_SEC")
    jwt_shared_secret: str | None = Field(default=None, alias="JWT_SHARED_SECRET")
    jwt_clock_skew_sec: int = Field(default=30, alias="JWT_CLOCK_SKEW_SEC")
    jwt_service_claim_key: str = Field(default="token_type", alias="JWT_SERVICE_CLAIM_KEY")
    jwt_service_claim_values: str = Field(
        default="service,client_credentials,m2m", alias="JWT_SERVICE_CLAIM_VALUES"
    )
    jwt_service_role_claim: str = Field(default="roles", alias="JWT_SERVICE_ROLE_CLAIM")
    jwt_service_allowed_roles: str = Field(
        default="service,admin", alias="JWT_SERVICE_ALLOWED_ROLES"
    )
    jwt_service_permission_claim: str = Field(default="scope", alias="JWT_SERVICE_PERMISSION_CLAIM")
    jwt_service_required_scopes_admin_read: str = Field(
        default="agent.admin.read,agent.admin", alias="JWT_SERVICE_REQUIRED_SCOPES_ADMIN_READ"
    )
    jwt_service_required_scopes_admin_write: str = Field(
        default="agent.admin.write,agent.admin", alias="JWT_SERVICE_REQUIRED_SCOPES_ADMIN_WRITE"
    )
    jwt_service_required_scopes_ws_internal: str = Field(
        default="agent.ws.internal,agent.admin", alias="JWT_SERVICE_REQUIRED_SCOPES_WS_INTERNAL"
    )

    # -------------------------------------------------------------------------
    # Storage
    # -------------------------------------------------------------------------
    postgres_dsn: str = Field(
        default="postgresql+psycopg://postgres:postgres@postgres:5432/agent",
        alias="POSTGRES_DSN",
    )
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")

    chunks_dir: str = Field(default="./data/chunks", alias="CHUNKS_DIR")
    storage_mode: str = Field(default="local_fs", alias="STORAGE_MODE")  # local_fs|shared_fs
    storage_shared_fs_dir: str | None = Field(default=None, alias="STORAGE_SHARED_FS_DIR")
    storage_require_shared_in_prod: bool = Field(
        default=True, alias="STORAGE_REQUIRE_SHARED_IN_PROD"
    )
    # -------------------------------------------------------------------------
    # PII / Retention
    # -------------------------------------------------------------------------
    pii_masking: bool = Field(default=True, alias="PII_MASKING")
    retention_days_audio: int = Field(default=14, alias="RETENTION_DAYS_AUDIO")
    retention_days_text: int = Field(default=90, alias="RETENTION_DAYS_TEXT")

    # -------------------------------------------------------------------------
    # STT
    # -------------------------------------------------------------------------
    stt_provider: str = Field(
        default="whisper_local", alias="STT_PROVIDER"
    )  # whisper_local|google|salutespeech

    # Локальный Whisper (faster-whisper)
    whisper_model_size: str = Field(
        default="small", alias="WHISPER_MODEL_SIZE"
    )  # tiny|base|small|medium|large-v3
    whisper_device: str = Field(default="cpu", alias="WHISPER_DEVICE")  # cpu|cuda
    whisper_compute_type: str = Field(
        default="int8", alias="WHISPER_COMPUTE_TYPE"
    )  # int8|int8_float16|float16|float32
    whisper_language: str = Field(default="ru", alias="WHISPER_LANGUAGE")  # ru|en|auto
    whisper_vad_filter: bool = Field(
        default=True, alias="WHISPER_VAD_FILTER"
    )  # VAD для улучшения качества сегментов
    whisper_beam_size: int = Field(default=1, alias="WHISPER_BEAM_SIZE")

    # -------------------------------------------------------------------------
    # Meeting connector (SberJazz target)
    # -------------------------------------------------------------------------
    meeting_connector_provider: str = Field(
        default="sberjazz_mock", alias="MEETING_CONNECTOR_PROVIDER"
    )  # sberjazz|sberjazz_mock|none
    meeting_auto_join_on_start: bool = Field(default=False, alias="MEETING_AUTO_JOIN_ON_START")
    sberjazz_api_base: str | None = Field(default=None, alias="SBERJAZZ_API_BASE")
    sberjazz_api_token: str | None = Field(default=None, alias="SBERJAZZ_API_TOKEN")
    sberjazz_timeout_sec: int = Field(default=10, alias="SBERJAZZ_TIMEOUT_SEC")
    sberjazz_http_retries: int = Field(default=2, alias="SBERJAZZ_HTTP_RETRIES")
    sberjazz_http_retry_backoff_ms: int = Field(default=300, alias="SBERJAZZ_HTTP_RETRY_BACKOFF_MS")
    sberjazz_http_retry_statuses: str = Field(
        default="408,409,425,429,500,502,503,504",
        alias="SBERJAZZ_HTTP_RETRY_STATUSES",
    )
    sberjazz_retries: int = Field(default=2, alias="SBERJAZZ_RETRIES")
    sberjazz_retry_backoff_ms: int = Field(default=300, alias="SBERJAZZ_RETRY_BACKOFF_MS")
    sberjazz_cb_failure_threshold: int = Field(default=5, alias="SBERJAZZ_CB_FAILURE_THRESHOLD")
    sberjazz_cb_open_sec: int = Field(default=60, alias="SBERJAZZ_CB_OPEN_SEC")
    sberjazz_op_lock_ttl_sec: int = Field(default=60, alias="SBERJAZZ_OP_LOCK_TTL_SEC")
    sberjazz_cb_auto_reset_enabled: bool = Field(
        default=True, alias="SBERJAZZ_CB_AUTO_RESET_ENABLED"
    )
    sberjazz_cb_auto_reset_min_age_sec: int = Field(
        default=30, alias="SBERJAZZ_CB_AUTO_RESET_MIN_AGE_SEC"
    )
    sberjazz_session_ttl_sec: int = Field(default=86_400, alias="SBERJAZZ_SESSION_TTL_SEC")
    sberjazz_reconcile_stale_sec: int = Field(default=900, alias="SBERJAZZ_RECONCILE_STALE_SEC")
    reconciliation_enabled: bool = Field(default=True, alias="RECONCILIATION_ENABLED")
    reconciliation_interval_sec: int = Field(default=60, alias="RECONCILIATION_INTERVAL_SEC")
    reconciliation_limit: int = Field(default=200, alias="RECONCILIATION_LIMIT")

    # -------------------------------------------------------------------------
    # LLM (OpenAI-compatible)
    # -------------------------------------------------------------------------
    llm_enabled: bool = Field(default=True, alias="LLM_ENABLED")
    openai_api_base: str | None = Field(default=None, alias="OPENAI_API_BASE")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    llm_model_id: str = Field(default="llama3:8b", alias="LLM_MODEL_ID")
    llm_temperature: float = Field(default=0.7, alias="LLM_TEMPERATURE")
    llm_max_tokens: int = Field(default=2048, alias="LLM_MAX_TOKENS")
    llm_top_p: float = Field(default=0.95, alias="LLM_TOP_P")
    llm_frequency_penalty: float = Field(default=0.0, alias="LLM_FREQUENCY_PENALTY")
    llm_presence_penalty: float = Field(default=0.0, alias="LLM_PRESENCE_PENALTY")
    llm_request_timeout_sec: int = Field(default=30, alias="LLM_REQUEST_TIMEOUT_SEC")
    llm_retries: int = Field(default=2, alias="LLM_RETRIES")
    llm_retry_backoff_ms: int = Field(default=250, alias="LLM_RETRY_BACKOFF_MS")

    # -------------------------------------------------------------------------
    # OTEL (OpenTelemetry)
    # -------------------------------------------------------------------------
    otel_enabled: bool = Field(default=False, alias="OTEL_ENABLED")
    otel_exporter_otlp_endpoint: str | None = Field(
        default=None, alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )

    # Delivery / SMTP
    # -------------------------------------------------------------------------
    delivery_provider: str = Field(default="email", alias="DELIVERY_PROVIDER")
    smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str | None = Field(default=None, alias="SMTP_USER")
    smtp_pass: str | None = Field(default=None, alias="SMTP_PASS")
    email_from: str = Field(default="hr-agent@example.com", alias="EMAIL_FROM")

    # -------------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------------
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="json", alias="LOG_FORMAT")  # json|text
    security_audit_db_enabled: bool = Field(default=True, alias="SECURITY_AUDIT_DB_ENABLED")
    readiness_fail_fast_in_prod: bool = Field(default=True, alias="READINESS_FAIL_FAST_IN_PROD")


_SETTINGS = Settings()


def get_settings() -> Settings:
    return _SETTINGS
