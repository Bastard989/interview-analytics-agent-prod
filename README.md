# Interview Analytics Agent

Script-first агент для записи интервью/встреч и подготовки аналитики для тех, кто не присутствовал на звонке.

## Что умеет агент

- записывает встречу в `mp3`;
- делает транскрипт (опционально);
- строит локальные отчеты: `report.json` и `report.txt`;
- может отправить запись в API-пайплайн для расширенной аналитики:
  - `scorecard`,
  - `decision`,
  - `senior brief`,
  - `comparison`;
- работает в foreground и background режимах;
- поддерживает graceful stop, чтобы не терять артефакты при остановке.

## Быстрый старт

```bash
git clone <your-repo-url>
cd interview-analytics-agent-prod2
```

### 1) Подготовка окружения

```bash
make setup-local
```

Что делает команда:
- создает `.venv`;
- устанавливает зависимости из `requirements.txt`;
- создает `.env` из `.env.example` (если `.env` еще нет);
- проверяет наличие `ffmpeg`.

### 2) Запуск API

```bash
make api-local
```

Проверка:
- `http://127.0.0.1:8010/health`

### 3) Запуск записи встречи

Foreground (простой вариант):

```bash
make agent-run URL="https://your-meeting-link" DURATION_SEC=900
```

Background:

```bash
make agent-start URL="https://your-meeting-link" DURATION_SEC=900
make agent-status
make agent-stop
```

## Как работает агент во время записи

Foreground (`agent-run`):
- запись идет в текущем терминале;
- после завершения сразу виден итог.

Background (`agent-start`):
- запись идет в отдельном процессе;
- `agent-status` показывает состояние и последние логи;
- `agent-stop` сначала выполняет корректную остановку через stop-flag,
  и только при таймауте делает signal-based остановку.

## Что на выходе

Артефакты по умолчанию сохраняются в `recordings/`:

- `<timestamp>.mp3`
- `<timestamp>.txt` (если включена транскрибация)
- `<timestamp>.report.json`
- `<timestamp>.report.txt`

## Команды (сводно)

```bash
# подготовка
make setup-local

# API
make api-local

# запись
make agent-run URL="https://..." DURATION_SEC=900
make agent-start URL="https://..." DURATION_SEC=900
make agent-status
make agent-stop

# прямой quick recorder
make quick-record URL="https://..."
```

Альтернатива через wrapper:

```bash
./scripts/agent.sh run "https://..." 900
./scripts/agent.sh start "https://..." 900
./scripts/agent.sh status
./scripts/agent.sh stop
```

## Частые сценарии

Явный выбор устройства записи:

```bash
INPUT_DEVICE="BlackHole 2ch" make agent-run URL="https://..." DURATION_SEC=900
```

Загрузка записи в API-пайплайн:

```bash
AGENT_BASE_URL="http://127.0.0.1:8010" \
AGENT_API_KEY="your-api-key" \
make agent-start URL="https://..." DURATION_SEC=1200
```

## Production checklist

Перед production запуском:

1. Настрой секреты и ключи в `.env`/secret manager.
2. Установи корректный `AUTH_MODE` и production API keys/JWT.
3. Настрой SMTP (если нужна ручная email-доставка).
4. Проверь `ffmpeg` и аудио-устройство на хосте записи.
5. Прогони базовые тесты и smoke.

## Техническое описание

### Основные компоненты

- `scripts/setup_local.sh` — bootstrap окружения.
- `scripts/agent.sh` — удобный CLI wrapper.
- `scripts/meeting_agent.py` — orchestration (`run/start/status/stop`).
- `scripts/quick_record_meeting.py` — script-first запуск записи.
- `src/interview_analytics_agent/quick_record.py` — core логика quick record.
- `apps/api_gateway` — API слой для ingestion/артефактов/аналитики.

### Pipeline

1. Preflight-check (`ffmpeg`, устройство, права, свободное место).
2. Захват аудио сегментами с overlap.
3. Сборка финального `mp3`.
4. Опциональная транскрибация (`faster-whisper`).
5. Построение локального `report`.
6. Опциональная отправка в `/v1` API.
7. Опциональная ручная email-доставка.

### Основные API endpoints

- `POST /v1/quick-record/start`
- `GET /v1/quick-record/status`
- `POST /v1/quick-record/stop`
- `GET /v1/meetings`
- `GET /v1/meetings/{meeting_id}`
- `GET /v1/meetings/{meeting_id}/report`
- `GET /v1/meetings/{meeting_id}/scorecard`
- `GET /v1/meetings/{meeting_id}/decision`
- `GET /v1/meetings/{meeting_id}/senior-brief`
- `POST /v1/analysis/comparison`
- `POST /v1/meetings/{meeting_id}/delivery/manual`

### Полезные переменные окружения

- `INPUT_DEVICE` — устройство захвата.
- `AGENT_BASE_URL` — URL API.
- `AGENT_API_KEY` — API key для upload.
- `OUTPUT_DIR` — директория артефактов (`recordings` по умолчанию).
- `TRANSCRIBE=1` — включить транскрибацию.
- `UPLOAD_TO_AGENT=1` — отправлять запись в API.

## Тестирование

```bash
# unit
python3 -m pytest tests/unit -q

# script-first integration
python3 -m pytest tests/integration/test_script_first_agent.py -q

# local API smoke
python3 tools/e2e_local.py
```

## Минимальные требования

- Python `3.11+`
- `ffmpeg`
- для macOS записи системного звука обычно требуется loopback device (например BlackHole)
