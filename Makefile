SHELL := /bin/bash

COMPOSE ?= docker compose
API_SERVICE ?= api-gateway
PYTHON ?= python3

.PHONY: \
	doctor up down ps logs migrate smoke reset-db \
	compose-up compose-down \
	fmt lint fix test storage-smoke \
	cycle cycle-autofix \
	openapi-gen openapi-check release-check

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
