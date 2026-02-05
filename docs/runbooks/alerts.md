# Alert Runbook

## Проверка доставки алертов (smoke)

1. Поднять observability-профиль: `docker compose --profile observability up -d`.
2. Запустить smoke: `make alerts-smoke`.
3. Проверить relay health: `curl -fsS http://localhost:9081/health`.
4. Проверить relay metrics: `curl -fsS http://localhost:9081/metrics`.
5. Проверить sink статистику: `curl -fsS http://localhost:9080/stats`.
6. Ожидаемо: минимум 1 `warning` и 1 `critical` событие.

Для production routing:
- Укажи внешние webhook URL через `ALERT_RELAY_*_TARGET_URL`.
- При необходимости включи shadow-доставку через `ALERT_RELAY_*_SHADOW_URL`.
- Для нестабильных внешних каналов настрой retry relay:
  `ALERT_RELAY_RETRIES`, `ALERT_RELAY_RETRY_BACKOFF_MS`, `ALERT_RELAY_RETRY_STATUSES`.

## AlertRelayDown

1. Проверить контейнер `alert-relay`: `docker compose ps alert-relay`.
2. Проверить логи: `docker compose logs --no-color --tail=300 alert-relay`.
3. Проверить `/health` и `/metrics` внутри контейнера.
4. После восстановления убедиться, что `up{job="alert-relay"} == 1`.

## AlertRelayDeliveryErrors

1. Проверить последние ошибки relay в логах `alert-relay` (target/shadow URL, тип ошибки).
2. Проверить доступность внешнего webhook-провайдера (Slack/PagerDuty/и т.д.).
3. При необходимости временно переключить доставку на shadow URL.
4. После фикса убедиться, что `increase(agent_alert_relay_forward_total{result="error"}[10m]) == 0`.

## AlertRelayRetriesHigh

1. Проверить метрику retry по labels (`channel`, `target`, `reason`).
2. Если причина `http_429`/`http_503` — проверить rate limits и SLA внешнего провайдера.
3. Увеличить `ALERT_RELAY_RETRY_BACKOFF_MS` и/или уменьшить частоту алертов.
4. Подтвердить снижение `increase(agent_alert_relay_retries_total[10m])`.

## ApiGatewayDown

1. Проверить состояние контейнера `api-gateway`: `docker compose ps`.
2. Посмотреть логи: `docker compose logs --no-color --tail=300 api-gateway`.
3. Проверить зависимости `postgres` и `redis`.
4. После восстановления убедиться, что `GET /health` возвращает `200`.

## QueueBacklogHigh

1. Проверить, какой именно queue растёт (`q:stt`, `q:enhancer`, `q:analytics`, ...).
2. Проверить соответствующий worker (`docker compose logs` для нужного сервиса).
3. Убедиться, что нет массовых ошибок провайдера (STT/LLM/connector).
4. Если backlog не снижается — временно увеличить число worker-реплик.

## DLQNotEmpty

1. Проверить payload сообщений в DLQ и определить первопричину.
2. Исправить ошибку обработки (код/конфиг/внешний провайдер).
3. После фикса выполнить controlled replay в основную очередь.
4. Проверить, что DLQ опустела и алерт закрылся.

## ApiP95LatencyHigh

1. Проверить p95 в Grafana для `/v1/*` и выделить проблемные endpoint'ы.
2. Проверить очередь и внешние провайдеры (STT/LLM/connector), нет ли деградации.
3. Проверить CPU/RAM и saturation у `api-gateway` и worker'ов.
4. При необходимости включить деградационный режим (ограничение тяжёлых операций).

## SberJazzConnectorUnhealthy

1. Проверить endpoint `GET /v1/admin/connectors/sberjazz/health`.
2. Проверить валидность `SBERJAZZ_API_BASE` и `SBERJAZZ_API_TOKEN`.
3. Проверить сетевую доступность SberJazz API из контейнера.
4. Запустить ручной reconnect проблемной сессии через admin API.

## SberJazzReconcileHasFailures

1. Проверить результат `POST /v1/admin/connectors/sberjazz/reconcile`.
2. Открыть список сессий `GET /v1/admin/connectors/sberjazz/sessions`.
3. Для проблемных встреч запустить targeted reconnect.
4. Если повторяется — анализировать ошибки коннектора и auth к SberJazz.

## SberJazzCircuitBreakerOpen

1. Проверить состояние breaker: `GET /v1/admin/connectors/sberjazz/circuit-breaker`.
2. Проверить первичную причину в логах `api-gateway` (`sberjazz_cb_failure`, ошибки провайдера).
3. Проверить доступность SberJazz API и валидность токена (`SBERJAZZ_API_TOKEN`).
4. После устранения причины дождаться auto-cooldown (`SBERJAZZ_CB_OPEN_SEC`) или выполнить manual reset:
   `POST /v1/admin/connectors/sberjazz/circuit-breaker/reset`.

## SberJazzLivePullHasFailures

1. Проверить ручной запуск live-pull: `POST /v1/admin/connectors/sberjazz/live-pull`.
2. Проверить список сессий: `GET /v1/admin/connectors/sberjazz/sessions`.
3. Проверить логи `api-gateway` по событиям `sberjazz_live_pull_retry`, `sberjazz_live_pull_failed`.
4. Если есть `invalid_chunks` — проверить формат ответа провайдера `/live-chunks`.

## BlobStorageUnhealthy

1. Проверить endpoint `GET /v1/admin/storage/health`.
2. Убедиться, что `STORAGE_MODE` и путь (`CHUNKS_DIR`/`STORAGE_SHARED_FS_DIR`) корректны.
3. Для `shared_fs` проверить mount и права записи на shared storage.
4. После восстановления убедиться, что `agent_storage_health` вернулся в `1`.

## SystemReadinessFailed

1. Проверить endpoint `GET /v1/admin/system/readiness`.
2. Исправить все `error`-issues (auth/storage/cors/OIDC).
3. Если есть `warning`-issues — зафиксировать план устранения до прод-релиза.
4. Убедиться, что метрика `agent_system_readiness` вернулась в `1`.
