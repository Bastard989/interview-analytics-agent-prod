# Alert Runbook

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
4. После устранения причины дождаться auto-cooldown (`SBERJAZZ_CB_OPEN_SEC`) и повторить reconnect.
