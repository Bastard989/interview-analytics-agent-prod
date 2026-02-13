# Interview Analytics Agent (script-first)

Агент для записи интервью/встреч по ссылке и генерации понятной аналитики для сеньоров, которые не присутствовали на звонке.

## Что делает агент

- записывает встречу в `mp3`;
- делает транскрипт (опционально);
- строит отчеты (`report.json`, `report.txt`);
- может отправить запись в API-пайплайн для расширенной аналитики (`scorecard`, `decision`, `senior brief`, `comparison`);
- поддерживает foreground и background режимы;
- корректно завершает запись через graceful stop.

## Быстрый старт (5 минут)

```bash
cd "/Users/kirill/Documents/New project/interview-analytics-agent-prod2"
```

1. Установи зависимости и подготовь окружение:

```bash
make setup-local
```

2. Запусти API (терминал №1):

```bash
make api-local
```

3. Запусти запись (терминал №2):

```bash
make agent-run URL="https://your-meeting-link" DURATION_SEC=900
```

## Основные команды

Из корня проекта:

```bash
# foreground (блокирующий режим)
make agent-run URL="https://..." DURATION_SEC=900

# background
make agent-start URL="https://..." DURATION_SEC=900
make agent-status
make agent-stop

# быстрый прямой запуск quick recorder
make quick-record URL="https://..."
```

Альтернатива через wrapper:

```bash
./scripts/agent.sh run "https://..." 900
./scripts/agent.sh start "https://..." 900
./scripts/agent.sh status
./scripts/agent.sh stop
```

## Как агент ведет себя во время работы

Foreground (`agent-run`):
- команда запускает запись и держит процесс в текущем терминале;
- после завершения сразу получаешь файлы результата.

Background (`agent-start`):
- запись идет в фоне;
- `agent-status` показывает состояние и последние логи;
- `agent-stop` сначала делает graceful-stop (через stop-flag), потом при таймауте отправляет сигнал.

Результаты сохраняются в `recordings/`:
- `<timestamp>.mp3`
- `<timestamp>.txt` (если включена транскрибация)
- `<timestamp>.report.json`
- `<timestamp>.report.txt`

## Частые сценарии

Запуск с явным устройством (macOS loopback):

```bash
INPUT_DEVICE="BlackHole 2ch" make agent-run URL="https://..." DURATION_SEC=900
```

Запуск с загрузкой в API:

```bash
AGENT_BASE_URL="http://127.0.0.1:8010" AGENT_API_KEY="dev-user-key" make agent-start URL="https://..." DURATION_SEC=1200
```

## Техническая часть (для инженеров)

### Ключевые компоненты

- `scripts/setup_local.sh`: bootstrap локального окружения;
- `scripts/agent.sh`: короткий CLI-wrapper;
- `scripts/meeting_agent.py`: orchestration (`run/start/status/stop`);
- `scripts/quick_record_meeting.py`: script-first запись;
- `src/interview_analytics_agent/quick_record.py`: core quick-record логика;
- `apps/api_gateway`: API для артефактов и аналитики.

### Pipeline внутри агента

1. Preflight-check: `ffmpeg`, устройство, права записи, свободное место.
2. Запись сегментов с overlap.
3. Корректная финализация сегментов в итоговый `mp3`.
4. Опциональная локальная транскрибация (`faster-whisper`).
5. Построение локального `report`.
6. Опциональная отправка в `/v1` API.
7. Опциональная ручная email-доставка артефактов.

### Основные API ручки

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

### Полезные ENV

- `INPUT_DEVICE` — устройство захвата (например `BlackHole 2ch`)
- `AGENT_BASE_URL` — URL локального API (обычно `http://127.0.0.1:8010`)
- `AGENT_API_KEY` — API key для upload в API
- `OUTPUT_DIR` — директория артефактов (по умолчанию `recordings`)
- `TRANSCRIBE=1` — включить транскрибацию
- `UPLOAD_TO_AGENT=1` — отправлять в API

### Проверка работоспособности

```bash
# unit
python3 -m pytest tests/unit -q

# script-first интеграционный тест
python3 -m pytest tests/integration/test_script_first_agent.py -q

# smoke локального API
python3 tools/e2e_local.py
```

## Минимальные требования

- Python `3.11+`
- `ffmpeg`
- для macOS захвата системного звука обычно нужен loopback-девайс (например BlackHole)

## Важно

Проект сейчас заточен под script-first работу через терминал и ручной контроль записи/аналитики.
