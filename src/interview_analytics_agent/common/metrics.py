"""
Метрики Prometheus для сервиса.

Назначение:
- Экспорт /metrics
- Общие счётчики и гистограммы для всех стадий пайплайна
- Используется API Gateway и воркерами
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import FastAPI, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

# =============================================================================
# СЧЁТЧИКИ И МЕТРИКИ
# =============================================================================

# Общее количество HTTP-запросов
REQUESTS_TOTAL = Counter(
    "agent_requests_total",
    "Общее количество HTTP запросов",
    ["service", "route", "method", "status"],
)

HTTP_REQUEST_LATENCY_MS = Histogram(
    "agent_http_request_latency_ms",
    "Задержка HTTP запроса (мс)",
    ["service", "route", "method"],
    buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000),
)

# Задержки по стадиям пайплайна
PIPELINE_STAGE_LATENCY_MS = Histogram(
    "agent_pipeline_stage_latency_ms",
    "Задержка выполнения стадий пайплайна (мс)",
    ["service", "stage"],
    buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000),
)

# Обработка задач очередей
QUEUE_TASKS_TOTAL = Counter(
    "agent_queue_tasks_total",
    "Количество обработанных задач очереди",
    ["service", "queue", "result"],
)

QUEUE_DEPTH = Gauge(
    "agent_queue_depth",
    "Текущая глубина stream-очередей",
    ["queue"],
)

DLQ_DEPTH = Gauge(
    "agent_dlq_depth",
    "Текущая глубина DLQ stream-очередей",
    ["queue"],
)

QUEUE_PENDING = Gauge(
    "agent_queue_pending",
    "Текущее количество pending сообщений в consumer group",
    ["queue", "group"],
)

METRICS_COLLECTION_ERRORS_TOTAL = Counter(
    "agent_metrics_collection_errors_total",
    "Ошибки сборки служебных метрик",
    ["source"],
)

SBERJAZZ_SESSIONS_TOTAL = Gauge(
    "agent_sberjazz_sessions_total",
    "Количество SberJazz connector-сессий",
    ["state"],  # connected|disconnected
)

SBERJAZZ_CONNECTOR_HEALTH = Gauge(
    "agent_sberjazz_connector_health",
    "Состояние SberJazz connector (1=healthy, 0=unhealthy)",
)

SBERJAZZ_CIRCUIT_BREAKER_OPEN = Gauge(
    "agent_sberjazz_circuit_breaker_open",
    "Состояние circuit breaker SberJazz connector (1=open, 0=closed/half_open)",
)

SBERJAZZ_RECONCILE_RUNS_TOTAL = Counter(
    "agent_sberjazz_reconcile_runs_total",
    "Количество запусков reconcile для SberJazz connector",
    ["source", "result"],  # source=job|admin, result=ok|failed
)

SBERJAZZ_RECONCILE_LAST_STALE = Gauge(
    "agent_sberjazz_reconcile_last_stale",
    "Количество stale-сессий в последнем reconcile запуске",
)

SBERJAZZ_RECONCILE_LAST_FAILED = Gauge(
    "agent_sberjazz_reconcile_last_failed",
    "Количество failed reconnect в последнем reconcile запуске",
)

SBERJAZZ_RECONCILE_LAST_RECONNECTED = Gauge(
    "agent_sberjazz_reconcile_last_reconnected",
    "Количество успешных reconnect в последнем reconcile запуске",
)


_QUEUE_GROUPS = {
    "q:stt": "g:stt",
    "q:enhancer": "g:enhancer",
    "q:analytics": "g:analytics",
    "q:delivery": "g:delivery",
    "q:retention": "g:retention",
}


@contextmanager
def track_stage_latency(service: str, stage: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        PIPELINE_STAGE_LATENCY_MS.labels(service=service, stage=stage).observe(elapsed_ms)


def _stream_len(r, stream: str) -> int:
    try:
        return int(r.xlen(stream))
    except Exception:
        return 0


def _xpending_count(r, stream: str, group: str) -> int:
    try:
        pending = r.xpending(stream, group)
        if isinstance(pending, dict):
            return int(pending.get("pending", 0))
    except Exception:
        return 0
    return 0


def refresh_queue_metrics() -> None:
    try:
        from interview_analytics_agent.queue.redis import redis_client
        from interview_analytics_agent.queue.streams import stream_dlq_name

        r = redis_client()
        for queue, group in _QUEUE_GROUPS.items():
            QUEUE_DEPTH.labels(queue=queue).set(_stream_len(r, queue))
            DLQ_DEPTH.labels(queue=queue).set(_stream_len(r, stream_dlq_name(queue)))
            QUEUE_PENDING.labels(queue=queue, group=group).set(_xpending_count(r, queue, group))
    except Exception:
        METRICS_COLLECTION_ERRORS_TOTAL.labels(source="queue_metrics").inc()


def refresh_connector_metrics() -> None:
    try:
        from interview_analytics_agent.services.sberjazz_service import (
            get_sberjazz_circuit_breaker_state,
            get_sberjazz_connector_health,
            list_sberjazz_sessions,
        )

        sessions = list_sberjazz_sessions(limit=2000)
        connected = sum(1 for s in sessions if s.connected)
        disconnected = max(0, len(sessions) - connected)
        SBERJAZZ_SESSIONS_TOTAL.labels(state="connected").set(connected)
        SBERJAZZ_SESSIONS_TOTAL.labels(state="disconnected").set(disconnected)

        health = get_sberjazz_connector_health()
        SBERJAZZ_CONNECTOR_HEALTH.set(1 if health.healthy else 0)
        cb = get_sberjazz_circuit_breaker_state()
        SBERJAZZ_CIRCUIT_BREAKER_OPEN.set(1 if cb.state == "open" else 0)
    except Exception:
        METRICS_COLLECTION_ERRORS_TOTAL.labels(source="connector_metrics").inc()


def record_sberjazz_reconcile_result(
    *,
    source: str,
    stale: int,
    failed: int,
    reconnected: int,
) -> None:
    result = "failed" if failed > 0 else "ok"
    SBERJAZZ_RECONCILE_RUNS_TOTAL.labels(source=source, result=result).inc()
    SBERJAZZ_RECONCILE_LAST_STALE.set(max(0, stale))
    SBERJAZZ_RECONCILE_LAST_FAILED.set(max(0, failed))
    SBERJAZZ_RECONCILE_LAST_RECONNECTED.set(max(0, reconnected))


# =============================================================================
# ENDPOINT /metrics
# =============================================================================
def setup_metrics_endpoint(app: FastAPI) -> None:
    """
    Регистрирует endpoint /metrics для Prometheus.
    """

    @app.middleware("http")
    async def http_metrics(request: Request, call_next):
        route = request.url.path
        method = request.method
        started = time.perf_counter()

        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        status_code = str(response.status_code)

        REQUESTS_TOTAL.labels(
            service="api-gateway",
            route=route,
            method=method,
            status=status_code,
        ).inc()
        HTTP_REQUEST_LATENCY_MS.labels(
            service="api-gateway",
            route=route,
            method=method,
        ).observe(elapsed_ms)
        return response

    @app.get("/metrics")
    def metrics() -> Response:
        refresh_queue_metrics()
        refresh_connector_metrics()
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
