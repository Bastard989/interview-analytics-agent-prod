# Performance Runbook

## Load guardrail (локально)

1. Поднять стек в быстром режиме:
   `APP_ENV=dev AUTH_MODE=api_key API_KEYS=dev-user-key SERVICE_API_KEYS=dev-service-key STT_PROVIDER=mock LLM_ENABLED=false docker compose up -d --build`
2. Запустить guardrail:
   `python3 tools/realtime_load_guardrail.py --base-url http://127.0.0.1:8010 --user-key dev-user-key --service-key dev-service-key`
   - если нужно падать при недоступности admin-checks: добавь `--strict-admin-checks`.
3. Запустить WS guardrail:
   `python3 tools/ws_contours_guardrail.py --base-url http://127.0.0.1:8010 --ws-base-url ws://127.0.0.1:8010 --user-key dev-user-key --service-key dev-service-key --strict-split-check`
4. Быстрый профиль как в CI:
   `make perf-guardrail-lite`
5. Проверить результат:
   - stdout: `load guardrail OK|FAILED`
   - отчеты: `reports/realtime_load_guardrail.json`, `reports/ws_contours_guardrail.json`

## Guardrails для real-коннектора (SberJazz)

Если тестируешь продовый контур:

- `make load-guardrail-real`
- `make ws-guardrail-real`

Требования:
- `MEETING_CONNECTOR_PROVIDER=sberjazz`
- валидные `API_KEYS` и `SERVICE_API_KEYS`

## CI latency gate (light)

- В `CI` и `Release` используется облегченный performance gate на `STT_PROVIDER=mock`.
- Проверяются `realtime_load_guardrail` и `ws_contours_guardrail` с уменьшенной нагрузкой
  (быстрый fail-fast на явный latency regression).
- В light-профиле включён strict admin-check: `total_dlq_depth` и доступность `/v1/admin/queues/health`.
- Отчеты сохраняются в:
  `reports/realtime_load_guardrail_ci.json`, `reports/ws_contours_guardrail_ci.json`.

## Что проверяется

- `failure_rate` — доля встреч, где pipeline не дошел до `report`.
- `p95_ingest_ms` — p95 latency HTTP ingest (`POST /v1/meetings/{id}/chunks`).
- `p95_e2e_ms` — p95 от первого chunk до готового `report`.
- `throughput_meetings_per_min` — успешные встречи в минуту.
- `total_dlq_depth` — суммарная глубина DLQ (если передан `--service-key`).
- `auth_split` — корректный deny для смешивания ролей WS-контуров.
- `p95_ws_send_ms` — p95 latency `ws.send()` при ingest в `/v1/ws` и `/v1/ws/internal`.

## Действия, если guardrail упал

1. Проверить `failed_meetings`/`failed_runs` и `checks` в JSON-отчете.
2. Проверить admin endpoint: `GET /v1/admin/queues/health`.
3. Проверить метрики и backlog:
   - `/metrics`
   - Prometheus/Grafana (если поднят observability profile)
4. Проверить логи `api-gateway` и worker'ов:
   `docker compose logs --no-color --tail=300 api-gateway worker-stt worker-enhancer worker-analytics worker-delivery`
