# Interview Analytics Agent

Production-ориентированный backend для транскрибации и аналитики интервью.

## Быстрый старт (dev)

- `docker compose up -d --build`
- Проверка API: `http://localhost:8010/health`
- Метрики: `http://localhost:8010/metrics`

## E2E Smoke

- `python3 tools/e2e_local.py`

Сценарий smoke:
1. `POST /v1/meetings/start`
2. `POST /v1/meetings/{id}/chunks`
3. `GET /v1/meetings/{id}` -> `enhanced_transcript` + `report`

Контуры WebSocket:
- `/v1/ws` — пользовательский контур (user JWT / `API_KEYS`).
- `/v1/ws/internal` — сервисный контур (service API key / service JWT claims).
  Для service JWT дополнительно требуется scope из `JWT_SERVICE_REQUIRED_SCOPES_WS_INTERNAL`.

## Режимы авторизации

- `AUTH_MODE=none` — только для local/dev
- `AUTH_MODE=api_key` — статические API ключи
- `AUTH_MODE=jwt` — JWT/OIDC + опциональный fallback на service API key

## Внутренний Admin API (только service)

- `GET /v1/admin/queues/health` — состояние queue/DLQ/pending.
- `POST /v1/admin/connectors/sberjazz/{meeting_id}/join` — инициировать live-подключение коннектора.
- `GET /v1/admin/connectors/sberjazz/{meeting_id}/status` — получить текущий статус подключения.
- `POST /v1/admin/connectors/sberjazz/{meeting_id}/leave` — завершить подключение.
- `POST /v1/admin/connectors/sberjazz/{meeting_id}/reconnect` — принудительный reconnect.
- `GET /v1/admin/connectors/sberjazz/health` — health/probe коннектора.
- `GET /v1/admin/connectors/sberjazz/circuit-breaker` — текущее состояние circuit breaker.
- `GET /v1/admin/connectors/sberjazz/sessions` — список сохранённых connector-сессий.
- `POST /v1/admin/connectors/sberjazz/reconcile` — reconcile stale-сессий с авто-reconnect.
- `GET /v1/admin/security/audit` — получить персистентный audit trail (allow/deny).
- Требуется service-авторизация (`SERVICE_API_KEYS`) или service JWT claims:
  (`JWT_SERVICE_CLAIM_KEY` / `JWT_SERVICE_CLAIM_VALUES`, `JWT_SERVICE_ROLE_CLAIM` / `JWT_SERVICE_ALLOWED_ROLES`).
- Для service JWT включена scope-политика:
  - read endpoint'ы: `JWT_SERVICE_REQUIRED_SCOPES_ADMIN_READ`
  - write endpoint'ы: `JWT_SERVICE_REQUIRED_SCOPES_ADMIN_WRITE`

Security audit логи:
- `security_audit_allow` и `security_audit_deny` (endpoint, method, subject, auth_type, reason).
- Персистентный аудит в БД (`security_audit_events`), отключается через `SECURITY_AUDIT_DB_ENABLED=false`.

## Reconciliation worker

- `worker-reconciliation` запускает авто-reconnect stale connector-сессий.
- Настройки: `RECONCILIATION_ENABLED`, `RECONCILIATION_INTERVAL_SEC`, `RECONCILIATION_LIMIT`,
  `SBERJAZZ_RECONCILE_STALE_SEC`.

## Стек наблюдаемости (опциональный профиль)

Запуск:

- `docker compose --profile observability up -d`

Сервисы:
- Prometheus: `http://localhost:9090`
- Alertmanager: `http://localhost:9093`
- Grafana: `http://localhost:3000`

Дополнительные connector-метрики:
- `agent_sberjazz_connector_health`
- `agent_sberjazz_circuit_breaker_open`
- `agent_sberjazz_sessions_total{state="connected|disconnected"}`

## CI

GitHub Actions запускает:
- security scans (`trivy` + `grype`, fail на HIGH/CRITICAL),
- compose build + healthcheck,
- unit tests + lint + smoke cycle,
- OpenAPI contract check.

## Runbooks

- Алерты и действия при инцидентах: `docs/runbooks/alerts.md`
