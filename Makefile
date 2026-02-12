SHELL := /bin/bash

COMPOSE ?= docker compose
API_SERVICE ?= api-gateway
PYTHON ?= python3

.PHONY: \
	doctor up down ps logs migrate smoke reset-db \
	compose-up compose-down \
	fmt lint fix test storage-smoke quick-record \
	cycle cycle-autofix \
	openapi-gen openapi-check release-check alerts-rules-check alerts-smoke alert-relay-metrics-smoke alert-relay-failure-smoke alert-relay-retry-guardrail load-guardrail ws-guardrail perf-guardrail-lite e2e-connector-live e2e-connector-real

doctor:
	@echo "== docker ==" && docker version >/dev/null && echo "OK"
	@echo "== compose config ==" && $(COMPOSE) config >/dev/null && echo "OK"

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down --remove-orphans

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs --no-color --tail=200 $(API_SERVICE)

migrate:
	$(COMPOSE) build
	$(COMPOSE) run --rm -e RUN_MIGRATIONS=1 $(API_SERVICE) true

smoke:
	@echo "== compose config =="
	@$(COMPOSE) config >/dev/null
	@echo "== up =="
	@$(COMPOSE) up -d --build
	@echo "== wait /health =="
	@bash -lc 'for i in {1..60}; do curl -fsS http://localhost:8010/health >/dev/null && exit 0; sleep 1; done; exit 1'

reset-db:
	@echo "== DANGER: wipe volumes and rebuild =="
	$(COMPOSE) down -v --remove-orphans
	$(COMPOSE) up -d --build

# Backward-compatible aliases
compose-up: up
compose-down: down

fmt:
	$(COMPOSE) exec -T $(API_SERVICE) python -m ruff format --check .

lint:
	$(COMPOSE) exec -T $(API_SERVICE) python -m ruff check .

fix:
	$(COMPOSE) exec -T $(API_SERVICE) python -m ruff check --fix .
	$(COMPOSE) exec -T $(API_SERVICE) python -m ruff format .

test:
	$(COMPOSE) exec -T $(API_SERVICE) python -m pytest tests/unit -q

storage-smoke:
	$(PYTHON) tools/storage_failover_smoke.py

quick-record:
	@test -n "$(URL)" || (echo "Usage: make quick-record URL='https://meeting-link'"; exit 1)
	$(PYTHON) scripts/quick_record_meeting.py --url "$(URL)"

cycle:
	$(PYTHON) tools/ci_cycle.py

cycle-autofix:
	CYCLE_AUTOFIX=1 $(PYTHON) tools/ci_cycle.py

openapi-gen:
	$(COMPOSE) run --rm -T \
		-v "$$(pwd):/app" \
		-e PYTHONPATH=/app:/app/src \
		$(API_SERVICE) \
		python scripts/export_openapi.py

openapi-check:
	$(COMPOSE) run --rm -T \
		-v "$$(pwd):/app" \
		-e PYTHONPATH=/app:/app/src \
		$(API_SERVICE) \
		python scripts/check_openapi.py

release-check:
	$(PYTHON) scripts/check_release.py

alerts-rules-check:
	$(PYTHON) scripts/check_alert_rules.py

alerts-smoke:
	$(PYTHON) tools/alerts_delivery_smoke.py

alert-relay-metrics-smoke:
	$(PYTHON) tools/alert_relay_metrics_smoke.py

alert-relay-failure-smoke:
	$(PYTHON) tools/alert_relay_failure_policy_smoke.py --expected-status 502 --expect-fail-on-error true

alert-relay-retry-guardrail:
	$(PYTHON) tools/alert_relay_retry_guardrail.py --expected-status 502 --expect-fail-on-error true

load-guardrail:
	$(PYTHON) tools/realtime_load_guardrail.py

ws-guardrail:
	$(PYTHON) tools/ws_contours_guardrail.py

load-guardrail-real:
	MEETING_CONNECTOR_PROVIDER=sberjazz $(PYTHON) tools/realtime_load_guardrail.py --require-real-connector

ws-guardrail-real:
	MEETING_CONNECTOR_PROVIDER=sberjazz $(PYTHON) tools/ws_contours_guardrail.py --require-real-connector

perf-guardrail-lite:
	$(PYTHON) tools/realtime_load_guardrail.py \
		--base-url http://127.0.0.1:8010 \
		--user-key dev-user-key \
		--service-key dev-service-key \
		--meetings 8 \
		--concurrency 4 \
		--chunks-per-meeting 2 \
		--report-timeout-sec 90 \
		--max-failure-rate 0.15 \
		--max-p95-ingest-ms 600 \
		--max-p95-e2e-ms 20000 \
		--min-throughput-meetings-per-min 6 \
		--max-total-dlq-depth 0 \
		--strict-admin-checks \
		--report-json reports/realtime_load_guardrail_ci.json
	$(PYTHON) tools/ws_contours_guardrail.py \
		--base-url http://127.0.0.1:8010 \
		--ws-base-url ws://127.0.0.1:8010 \
		--user-key dev-user-key \
		--service-key dev-service-key \
		--meetings-per-contour 4 \
		--concurrency 4 \
		--chunks-per-meeting 2 \
		--report-timeout-sec 90 \
		--max-failure-rate 0.15 \
		--max-p95-ws-send-ms 200 \
		--max-p95-e2e-ms 20000 \
		--strict-split-check \
		--report-json reports/ws_contours_guardrail_ci.json

e2e-connector-live:
	$(PYTHON) tools/e2e_connector_live.py --provider sberjazz_mock

e2e-connector-real:
	$(PYTHON) tools/e2e_connector_live.py --provider sberjazz --require-report
