# Interview Analytics Agent

Production-ориентированный backend для транскрибации и аналитики интервью.

## Быстрый старт (dev)

- `docker compose up -d --build`
- Проверка API: `http://localhost:8010/health`
- Метрики: `http://localhost:8010/metrics`

Минимальный `.env` для старта (остальное имеет безопасные default):
- `APP_ENV=dev`
- `AUTH_MODE=api_key`
- `API_KEYS=dev-user-key`
- `SERVICE_API_KEYS=dev-service-key`

## E2E Smoke

- `python3 tools/e2e_local.py`
- `make e2e-connector-live` (realtime connector live-pull smoke в `sberjazz_mock`)
- `make e2e-connector-real` (real SberJazz smoke, требует `SBERJAZZ_API_BASE` и `SBERJAZZ_API_TOKEN`)
- `make storage-smoke` (shared storage failover smoke)
- `make alerts-smoke` (проверка маршрутизации warning/critical алертов через Alertmanager в webhook sink;
  требует `docker compose --profile observability up -d`)
- `make load-guardrail` (нагрузочный guardrail по latency/error-rate/throughput; отчет в `reports/realtime_load_guardrail.json`)
  - для строгой проверки admin-контуров добавь `--strict-admin-checks`.
- `make ws-guardrail` (нагрузочный guardrail по WS-контурам `/v1/ws` и `/v1/ws/internal`;
  отчет в `reports/ws_contours_guardrail.json`)

Real connector smoke (ручной запуск):
- `SBERJAZZ_API_BASE=https://... SBERJAZZ_API_TOKEN=... make e2e-connector-real`
- По умолчанию smoke требует готовый `report`; можно ослабить проверку запуском без `--require-report`.

Сценарий smoke:
1. `POST /v1/meetings/start`
2. `POST /v1/meetings/{id}/chunks`
3. `GET /v1/meetings/{id}` -> `enhanced_transcript` + `report`

`POST /v1/meetings/start`:
- поддерживает `auto_join_connector=true|false` (явное управление auto-join).
- если поле не передано, для `mode=realtime` используется `MEETING_AUTO_JOIN_ON_START`.
- при успешном auto-join в ответе возвращаются `connector_auto_join`, `connector_provider`, `connector_connected`.

Контуры WebSocket:
- `/v1/ws` — пользовательский контур (user JWT / `API_KEYS`).
- `/v1/ws/internal` — сервисный контур (service API key / service JWT claims).
  Для service JWT дополнительно требуется scope из `JWT_SERVICE_REQUIRED_SCOPES_WS_INTERNAL`.

HTTP ingest контуры:
- `/v1/meetings/{meeting_id}/chunks` — пользовательский/общий ingest.
- `/v1/internal/meetings/{meeting_id}/chunks` — только service-auth ingest (внутренний контур).
- Коннекторный live-ingest использует тот же ingest service/path (единая точка постановки chunk -> STT).
- Для трассировки можно передавать заголовок `X-Trace-Id` (32 hex), API возвращает тот же `X-Trace-Id` в ответе.
- Очереди и воркеры пробрасывают `trace_id/span_id`, а structured logs автоматически включают
  `trace_id`, `span_id`, `parent_span_id`, `meeting_id` (если известен).

## Режимы авторизации

- `AUTH_MODE=none` — только для local/dev
- `AUTH_MODE=api_key` — статические API ключи
- `AUTH_MODE=jwt` — JWT/OIDC + опциональный fallback на service API key
- В `APP_ENV=prod` при `AUTH_REQUIRE_JWT_IN_PROD=true` требуется `AUTH_MODE=jwt`.
- В `APP_ENV=prod` fallback на service API key автоматически отключается (только Bearer JWT).
- Для секретов поддерживаются `*_FILE` переменные (например `API_KEYS_FILE`, `SERVICE_API_KEYS_FILE`,
  `JWT_SHARED_SECRET_FILE`, `SBERJAZZ_API_TOKEN_FILE`).
- Multi-tenant (JWT only):
  - `TENANT_ENFORCEMENT_ENABLED=true` включает проверку tenant claim для user JWT.
  - Claim берётся из `TENANT_CLAIM_KEY` (по умолчанию `tenant_id`) и сохраняется в `context`
    под ключом `TENANT_CONTEXT_KEY`.
  - В этом режиме пользовательские API ключи не поддерживаются (нужен JWT с tenant claim).

## OIDC/JWT провайдеры (универсально)

Агент валидирует JWT по стандартному OIDC (issuer/jwks/audience). Поэтому подходит любой
провайдер уровня Keycloak/Auth0/Cognito.

Базовые переменные:
- `AUTH_MODE=jwt`
- `OIDC_ISSUER_URL=https://<issuer>/realms/<realm>` (или `OIDC_JWKS_URL=...`)
- `OIDC_AUDIENCE=<client_id>`
- `OIDC_ALGORITHMS=RS256`

### Роли и service‑доступ
Есть два способа распознать service‑JWT:
1) **Через роли**  
   - `JWT_SERVICE_ROLE_CLAIM=roles`  
   - `JWT_SERVICE_ALLOWED_ROLES=service,admin`
2) **Через тип токена (claim)**  
   - `JWT_SERVICE_CLAIM_KEY=token_type`  
   - `JWT_SERVICE_CLAIM_VALUES=service,client_credentials,m2m`

Scope‑проверки (для admin/write и /ws/internal):
- `JWT_SERVICE_PERMISSION_CLAIM=scope`
- `JWT_SERVICE_REQUIRED_SCOPES_ADMIN_READ=agent.admin.read,agent.admin`
- `JWT_SERVICE_REQUIRED_SCOPES_ADMIN_WRITE=agent.admin.write,agent.admin`
- `JWT_SERVICE_REQUIRED_SCOPES_WS_INTERNAL=agent.ws.internal,agent.admin`

Tenant‑изоляция (если включена):
- `TENANT_ENFORCEMENT_ENABLED=true`
- `TENANT_CLAIM_KEY=tenant_id`
- `TENANT_CONTEXT_KEY=tenant_id`

### Keycloak (пример)
1) Создай Realm и Client (OIDC).
2) Включи **Protocol Mapper**, чтобы вывести нужные claims в **top‑level** токена:
   - `tenant_id` (если используешь tenant‑изоляцию).
   - `roles` (или `token_type`) для service‑JWT.
3) Настрой переменные:
   - `OIDC_ISSUER_URL=https://<keycloak>/realms/<realm>`
   - `OIDC_AUDIENCE=<client_id>`
   - `JWT_SERVICE_ROLE_CLAIM=roles`
   - `JWT_SERVICE_ALLOWED_ROLES=service,admin`

### Auth0 (пример)
1) Создай API (audience) и Application.
2) Добавь custom claims (например `tenant_id`) через Rules/Actions.
3) Настрой переменные:
   - `OIDC_ISSUER_URL=https://<your-domain>.auth0.com/`
   - `OIDC_AUDIENCE=<api_audience>`
   - `JWT_SERVICE_CLAIM_KEY=token_type` и `JWT_SERVICE_CLAIM_VALUES=service`

### AWS Cognito (пример)
1) User Pool + App Client.
2) Добавь кастомный атрибут `custom:tenant_id` и маппинг в токен (top‑level).
3) Настрой переменные:
   - `OIDC_ISSUER_URL=https://cognito-idp.<region>.amazonaws.com/<user_pool_id>`
   - `OIDC_AUDIENCE=<app_client_id>`
   - `TENANT_CLAIM_KEY=tenant_id` (если mapped в top‑level)

Если не хочешь включать JWT сейчас — оставь `AUTH_MODE=api_key` и работай через `API_KEYS`.

## Secrets manager (Vault)

Поддерживается Vault KV (v2/v1). Секреты подгружаются **до** инициализации `Settings`.

Минимум (KV v2):
- `SECRETS_PROVIDER=vault`
- `VAULT_ADDR=https://vault.example.com`
- `VAULT_TOKEN=...` (или `VAULT_TOKEN_FILE=/run/secrets/vault_token`)
- `VAULT_KV_MOUNT=secret`
- `VAULT_SECRET_PATH=interview-agent`
- `VAULT_FIELD_MAP=API_KEYS=api_keys,SERVICE_API_KEYS=service_api_keys,JWT_SHARED_SECRET=jwt_shared_secret`

Опции:
- `VAULT_NAMESPACE` (enterprise namespace)
- `VAULT_TIMEOUT_SEC=5`
- `VAULT_KV_VERSION=2` (или `1`)
- `VAULT_SKIP_VERIFY=true` (если нужна выключенная проверка TLS)

Правило приоритета:
- если ENV уже задан, секрет **не перезаписывается**.

## Внутренний Admin API (только service)

- `GET /v1/admin/queues/health` — состояние queue/DLQ/pending.
  При частичных проблемах Redis endpoint возвращает `200` с per-queue `error`, не ломая весь ответ.
- `GET /v1/admin/storage/health` — healthcheck blob storage (режим, путь, read/write probe).
- `GET /v1/admin/system/readiness` — runtime readiness-check (prod-policy/конфигурация).
- `POST /v1/admin/connectors/sberjazz/{meeting_id}/join` — инициировать live-подключение коннектора.
- `GET /v1/admin/connectors/sberjazz/{meeting_id}/status` — получить текущий статус подключения.
- `POST /v1/admin/connectors/sberjazz/{meeting_id}/leave` — завершить подключение.
- `POST /v1/admin/connectors/sberjazz/{meeting_id}/reconnect` — принудительный reconnect.
- `GET /v1/admin/connectors/sberjazz/health` — health/probe коннектора.
- `GET /v1/admin/connectors/sberjazz/circuit-breaker` — текущее состояние circuit breaker.
- `POST /v1/admin/connectors/sberjazz/circuit-breaker/reset` — manual reset circuit breaker.
- `GET /v1/admin/connectors/sberjazz/sessions` — список сохранённых connector-сессий.
- `POST /v1/admin/connectors/sberjazz/reconcile` — reconcile stale-сессий с авто-reconnect.
- `POST /v1/admin/connectors/sberjazz/live-pull` — вручную запустить live-pull чанков из коннектора.
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
- Также выполняет live pull по активным connector-сессиям:
  `SBERJAZZ_LIVE_PULL_ENABLED`, `SBERJAZZ_LIVE_PULL_BATCH_LIMIT`, `SBERJAZZ_LIVE_PULL_SESSIONS_LIMIT`.
  Дополнительно: `SBERJAZZ_LIVE_PULL_RETRIES`, `SBERJAZZ_LIVE_PULL_RETRY_BACKOFF_MS`,
  `SBERJAZZ_LIVE_PULL_FAIL_RECONNECT_THRESHOLD` (авто-reconnect после N подряд live-pull ошибок по meeting).

## Startup readiness (prod guardrail)

- На старте `api-gateway` и все воркеры выполняют runtime readiness-check.
- В `APP_ENV=prod` при наличии readiness errors процесс завершится fail-fast
  (контролируется `READINESS_FAIL_FAST_IN_PROD=true|false`).
- Проверить текущее состояние можно через `GET /v1/admin/system/readiness`.

SberJazz HTTP resilience:
- `SBERJAZZ_HTTP_RETRIES`
- `SBERJAZZ_HTTP_RETRY_BACKOFF_MS`
- `SBERJAZZ_HTTP_RETRY_STATUSES`
- `SBERJAZZ_OP_LOCK_TTL_SEC` (защита от параллельных join/reconnect/leave для одной встречи)
- `SBERJAZZ_CB_AUTO_RESET_ENABLED` / `SBERJAZZ_CB_AUTO_RESET_MIN_AGE_SEC` (self-healing breaker через reconciliation worker)
- `SBERJAZZ_JOIN_IDEMPOTENT_TTL_SEC` (идемпотентный join: повторный join для свежей connected-сессии не дергает provider)
- Для non-retryable provider ошибок (auth/bad-request/invalid-response) join/leave/live-pull
  не тратят лишние retry-итерации (fail-fast).
- Для `MEETING_CONNECTOR_PROVIDER=sberjazz` в `APP_ENV=prod` readiness ожидает:
  `SBERJAZZ_API_BASE=https://...`, непустой `SBERJAZZ_API_TOKEN`, `AUTH_MODE=jwt`
  (`SBERJAZZ_REQUIRE_HTTPS_IN_PROD=true`).
- Startup probe для real SberJazz в prod:
  `SBERJAZZ_STARTUP_PROBE_ENABLED=true`, `SBERJAZZ_STARTUP_PROBE_FAIL_FAST_IN_PROD=true`.
- Если есть проблемы с сетью/IPv6, можно форсировать IPv4:
  `SBERJAZZ_FORCE_IPV4=true`.

## Tracing (OTEL)

- Базовая трассировка включена в ingress/queue/workers и коррелируется по `meeting_id`.
- При `OTEL_ENABLED=true` сервисы экспортируют OTLP spans в `OTEL_EXPORTER_OTLP_ENDPOINT`.
- Для HTTP можно передавать `X-Trace-Id` (32 hex), он возвращается в response header.
- Если OTEL-зависимости/endpoint недоступны, сервис продолжает работать (fail-safe no-op).

## Delivery (Email)

- При `DELIVERY_PROVIDER=email` отчёт отправляется по SMTP.
- Если доступны `raw_transcript` и `enhanced_transcript`, они отправляются во вложениях:
  `raw_transcript.txt` и `enhanced_transcript.txt`.

## Storage mode (production)

- `STORAGE_MODE=shared_fs` — production режим (shared POSIX storage, например managed NFS).
- `STORAGE_MODE=local_fs` — локальный режим для dev.
- В `APP_ENV=prod` при `STORAGE_REQUIRE_SHARED_IN_PROD=true` local storage запрещён.

## Kubernetes (baseline)

Базовые манифесты находятся в `deploy/k8s/base`.

Запуск:
- `kubectl apply -k deploy/k8s/base`
- `kubectl apply -k deploy/k8s/overlays/prod` (HPA + prod overrides)

Перед запуском:
- замените `image` в `deploy/k8s/base/*` на ваш registry/tag
  (release workflow публикует образ в `ghcr.io/<owner>/<repo>:<tag>`);
- заполните `deploy/k8s/base/secret.yaml` (DSN, Redis, API keys, токены);
- при необходимости поменяйте значения в `deploy/k8s/base/configmap.yaml`.

Примечания:
- PVC рассчитан на `ReadWriteMany` (shared FS); под ваш storage‑class может потребоваться корректировка.
- PostgreSQL/Redis ожидаются внешними (managed) и передаются через секреты.

## Стек наблюдаемости (опциональный профиль)

Запуск:

- `docker compose --profile observability up -d`

Сервисы:
- Prometheus: `http://localhost:9090`
- Alertmanager: `http://localhost:9093`
- Grafana: `http://localhost:3000`
- OTEL Collector (OTLP HTTP): `http://localhost:4318`
- Alert relay: `http://localhost:9081` (`/health`, `/webhook/{default|warning|critical}`)
- Alert webhook sink (dev/stage): `http://localhost:9080` (`/stats`, `/events`, `/reset`)

Дополнительные connector-метрики:
- `agent_sberjazz_connector_health`
- `agent_sberjazz_circuit_breaker_open`
- `agent_sberjazz_circuit_breaker_resets_total{source,reason}`
- `agent_sberjazz_sessions_total{state="connected|disconnected"}`
- `agent_sberjazz_live_pull_runs_total{source,result}`
- `agent_sberjazz_live_pull_last_scanned|connected|pulled|ingested|failed|invalid_chunks`
- `agent_storage_health{mode="local_fs|shared_fs"}`
- `agent_system_readiness`
- `agent_alert_relay_forward_total{channel,target,result}`
- `agent_alert_relay_retries_total{channel,target,reason}`
- `agent_alert_relay_forward_attempt_latency_ms`

Alert routing:
- Alertmanager использует severity-маршрутизацию (`default` / `warning` / `critical`).
- Alertmanager отправляет события в `alert-relay`, а relay маршрутизирует их дальше.
- В dev/stage relay по умолчанию отправляет во внутренний sink (`alert-webhook-sink`).
- Для production задай внешние URL через ENV:
  `ALERT_RELAY_DEFAULT_TARGET_URL`, `ALERT_RELAY_WARNING_TARGET_URL`,
  `ALERT_RELAY_CRITICAL_TARGET_URL`.
- Опционально можно включить shadow-доставку:
  `ALERT_RELAY_DEFAULT_SHADOW_URL`, `ALERT_RELAY_WARNING_SHADOW_URL`,
  `ALERT_RELAY_CRITICAL_SHADOW_URL`.
- Для устойчивости можно настроить retry-политику relay:
  `ALERT_RELAY_RETRIES`, `ALERT_RELAY_RETRY_BACKOFF_MS`,
  `ALERT_RELAY_RETRY_STATUSES` (по умолчанию 408/409/425/429/5xx).

## CI

GitHub Actions запускает:
- dependency review для PR,
- security scans (`pip-audit` + `trivy` + `grype`, fail на HIGH/CRITICAL),
- compose build + healthcheck,
- unit tests + lint + smoke cycle,
- OpenAPI contract check,
- pipeline latency guardrail (light) на `STT_PROVIDER=mock` (`realtime_load_guardrail` + `ws_contours_guardrail`),
- alert rules check (`promtool` + валидация runbook anchors),
- alert routing smoke (`warning`/`critical` delivery через Alertmanager -> alert-relay -> webhook sink),
- alert relay metrics smoke (`/metrics` + рост `agent_alert_relay_forward_total`),
- alert relay failure-policy smoke (проверка fail-closed/fail-open через `ALERT_RELAY_FAIL_ON_ERROR`),
- alert relay retry guardrail (проверка retry/backoff профиля и стабильности задержки).

Отдельный workflow `Performance Smoke` (nightly + manual):
- поднимает стек в `STT_PROVIDER=mock`,
- гоняет `tools/realtime_load_guardrail.py` и `tools/ws_contours_guardrail.py` с порогами,
- сохраняет артефакт `realtime-load-guardrail-report`.

Отдельный ручной workflow `Connector Real Smoke`:
- запускает `tools/e2e_connector_live.py --provider sberjazz --require-report`,
- требует repo secrets: `SBERJAZZ_API_BASE`, `SBERJAZZ_API_TOKEN`.

Release automation:
- workflow `Release` запускается на тегах формата `v*.*.*`,
- перед сборкой проверяет release policy (`tag == project.version`, валидный `openapi/openapi.json`),
- повторно выполняет build/test/lint/smoke/openapi-check + pipeline latency guardrail + alert routing smoke,
- собирает release assets (`sdist`, `wheel`, `openapi.json`, `SHA256SUMS`),
- публикует GitHub Release с автогенерируемыми release notes и provenance attestation.

Локальная проверка release policy:
- `make release-check`
- `make perf-guardrail-lite` (быстрый локальный performance gate)

## Runbooks

- Алерты и действия при инцидентах: `docs/runbooks/alerts.md`
- Производительность и guardrail-порогы: `docs/runbooks/performance.md`
- Real SberJazz smoke/приемка: `docs/runbooks/connector_real_smoke.md`
- Трассировка и OTEL smoke: `docs/runbooks/tracing.md`
