# Interview Analytics Agent

Production-oriented backend for interview transcription and analytics.

## Quick Start (dev)

- `docker compose up -d --build`
- API health: `http://localhost:8010/health`
- Metrics: `http://localhost:8010/metrics`

## E2E Smoke

- `python3 tools/e2e_local.py`

Smoke contract:
1. `POST /v1/meetings/start`
2. `POST /v1/meetings/{id}/chunks`
3. `GET /v1/meetings/{id}` -> `enhanced_transcript` + `report`

## Auth Modes

- `AUTH_MODE=none` — local/dev only
- `AUTH_MODE=api_key` — static API keys
- `AUTH_MODE=jwt` — JWT/OIDC + optional service API key fallback

## Observability Stack (optional profile)

Run with:

- `docker compose --profile observability up -d`

Services:
- Prometheus: `http://localhost:9090`
- Alertmanager: `http://localhost:9093`
- Grafana: `http://localhost:3000`

## CI

GitHub Actions runs build, healthcheck, tests, lint, and contract checks on push/PR.
