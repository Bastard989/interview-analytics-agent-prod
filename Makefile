SHELL := /bin/bash

COMPOSE ?= docker compose
API_SERVICE ?= api-gateway
PYTHON ?= python3
VENV_PYTHON ?= .venv/bin/python
API_HOST ?= 127.0.0.1
API_PORT ?= 8010
URL ?=
DURATION_SEC ?= 900

.PHONY: \
	doctor up down ps logs migrate smoke reset-db \
	setup-local api-local \
	agent-run agent-start agent-status agent-stop quick-record \
	lint fmt fix test e2e-local interview-guardrail

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

setup-local:
	./scripts/setup_local.sh

api-local:
	@test -x "$(VENV_PYTHON)" || (echo "Run 'make setup-local' first"; exit 1)
	PYTHONPATH="$(PWD):$(PWD)/src" \
	APP_ENV=dev \
	AUTH_MODE=api_key \
	API_KEYS=dev-user-key \
	SERVICE_API_KEYS=dev-service-key \
	QUEUE_MODE=inline \
	$(VENV_PYTHON) -m uvicorn apps.api_gateway.main:app --host $(API_HOST) --port $(API_PORT) --reload

agent-run:
	@test -n "$(URL)" || (echo "Usage: make agent-run URL='https://meeting-link' [DURATION_SEC=900]"; exit 1)
	./scripts/agent.sh run "$(URL)" "$(DURATION_SEC)"

agent-start:
	@test -n "$(URL)" || (echo "Usage: make agent-start URL='https://meeting-link' [DURATION_SEC=900]"; exit 1)
	./scripts/agent.sh start "$(URL)" "$(DURATION_SEC)"

agent-status:
	./scripts/agent.sh status

agent-stop:
	./scripts/agent.sh stop

quick-record:
	@test -n "$(URL)" || (echo "Usage: make quick-record URL='https://meeting-link'"; exit 1)
	@test -x "$(VENV_PYTHON)" || (echo "Run 'make setup-local' first"; exit 1)
	$(VENV_PYTHON) scripts/quick_record_meeting.py --url "$(URL)"

lint:
	python3 -m ruff check .

fmt:
	python3 -m ruff format --check .

fix:
	python3 -m ruff check --fix .
	python3 -m ruff format .

test:
	python3 -m pytest tests/unit -q

e2e-local:
	python3 tools/e2e_local.py

interview-guardrail:
	python3 tools/interview_regression_guardrail.py
