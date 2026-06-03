# PM Agent Platform

Мультиагентная платформа для автоматизации работы Project Manager.
Агент понимает запросы на естественном языке, работает с Яндекс Трекером и запрашивает подтверждение перед рискованными действиями.

**Документация:**
- [Архитектура системы](docs/ARCHITECTURE.md)
- [Как добавить нового агента](docs/ADDING_AGENTS.md)

---

## Быстрый старт

### Что нужно

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — менеджер зависимостей
- Docker + Docker Compose
- Yandex AI Studio: API key + folder ID
- Яндекс Трекер: OAuth token + org ID

### 1. Клонировать и установить

```bash
git clone https://github.com/Artem216/digital_breakthrough_2026.git
cd digital_breakthrough_2026
git checkout develop
uv sync --all-packages
```

### 2. Настроить переменные

```bash
cp packages/core/.env.example packages/core/.env
```

Заполнить в `packages/core/.env`:

```env
# Yandex Cloud (LLM)
YC_API_KEY=ваш_ключ
YC_FOLDER_ID=b1g...

# Яндекс Трекер
TRACKER_TOKEN=ваш_oauth_token
TRACKER_ORG_ID=ваш_org_id
TRACKER_ORG_TYPE=cloud          # или 360 для Яндекс 360
TRACKER_QUEUE=DARKHORSE         # ключ вашей очереди

# БД (для локального запуска без Docker)
DATABASE_URL=postgresql+asyncpg://pm_agent:pm_agent@localhost:5432/pm_agent

ENVIRONMENT=development
LOG_LEVEL=DEBUG
```

### 3. Запустить локально (без Docker)

```bash
# Терминал 1 — оркестратор (порт 8001)
uv run --package pm-orchestrator uvicorn pm_orchestrator.rpc:app --reload --port 8001

# Терминал 2 — HTTP API (порт 8000)
uv run --package platform-api uvicorn platform_api.main:app --reload --port 8000
```

### 4. Запустить в Docker

```bash
# Заполнить .env.test (скопировать из .env.example)
cp .env.example .env.test

docker compose -f docker-compose.yml -f docker-compose.test.yml --env-file .env.test up --build
```

Сервисы:

| Сервис | URL |
|--------|-----|
| Platform API | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| PM Orchestrator | http://localhost:8001 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |

---

## Использование API

### Отправить сообщение агенту

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "найди открытые задачи", "session_id": "demo"}'
```

### Создать задачу (вызовет confirm)

```bash
# 1. Отправляем запрос — получаем pending_confirm
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "заведи задачу: починить логин, приоритет critical", "session_id": "demo"}'

# В ответе: {"pending_confirm": {"confirm_id": "abc123", "prompt": "...", ...}}

# 2. Подтверждаем
curl -X POST http://localhost:8000/confirm/abc123 \
  -H "Content-Type: application/json" \
  -d '{"approved": true}'
```

### Конкретный агент

```bash
curl -X POST http://localhost:8000/agents/pm_agent/chat \
  -d '{"message": "привет", "session_id": "s1"}'
```

### Список агентов

```bash
curl http://localhost:8000/agents
# [{"name": "pm_agent", "description": "..."}]
```

### Лог действий

```bash
curl "http://localhost:8000/actions?session_id=demo"
```

---

## Добавить нового агента

1. Создать файл `services/pm-orchestrator/src/pm_orchestrator/agents/my_agent.py`
2. Определить класс, унаследованный от `BaseAgent`
3. Перезапустить оркестратор

Подробный гайд: [docs/ADDING_AGENTS.md](docs/ADDING_AGENTS.md)

---

## Разработка

### Тесты

```bash
# Все тесты
uv run pytest packages/core/tests/unit services/ -v

# С покрытием
uv run pytest packages/core/tests/unit --cov=core

# Smoke-тесты на реальный YandexGPT (нужны ключи)
cd packages/core && python -m pytest tests/smoke/ -v
```

### Линтер и форматирование

```bash
uv run ruff check .          # проверить
uv run ruff check --fix .    # автофикс
uv run ruff format .         # форматировать
```

### Добавить зависимость

```bash
uv add --package pm-orchestrator requests
uv add --package platform-api somelib
```

---

## Деплой на тест-сервер

```
feature/* → PR → develop → GitHub Actions → тест-сервер (автоматически)
```

**CI** (при каждом push/PR):
- `ruff check` + `ruff format --check`
- `pytest packages/core/tests/unit services/`

**CD** (при merge в develop):
- rsync кода на VPS
- `docker compose up --build`

### Статус деплоя

```bash
gh run list --limit 5
gh run watch
```

### Логи на сервере

```bash
ssh user@your-vps

docker logs test-pm-orchestrator-1 -f
docker logs test-platform-api-1 -f
```

---

## Структура проекта

```
digital_breakthrough_2026/
│
├── packages/core/               # Общая библиотека
│   └── src/core/
│       ├── agent.py             # BaseAgent, LLMSettings
│       ├── react.py             # ReActRunner (LLM → tool → confirm)
│       ├── llm.py               # YandexGPT клиент
│       ├── tools.py             # @platform_tool декоратор
│       ├── tracker.py           # Яндекс Трекер клиент
│       ├── tracker_tools.py     # Tracker инструменты для агентов
│       └── models.py            # SQLAlchemy ORM (11 таблиц)
│
├── services/
│   ├── pm-orchestrator/ :8001   # Мозг: агенты + JSON-RPC сервер
│   │   └── src/pm_orchestrator/
│   │       ├── agents/          # ← Добавить нового агента сюда
│   │       │   └── pm_agent.py
│   │       ├── orchestrator.py  # OrchestratorService
│   │       └── rpc.py           # JSON-RPC 2.0 endpoint
│   │
│   └── platform-api/ :8000      # Тонкий HTTP транспорт
│       └── src/platform_api/
│           ├── main.py          # FastAPI роуты
│           └── rpc_client.py    # Клиент оркестратора
│
├── monitoring/                  # Prometheus + Grafana + Alertmanager
├── docs/
│   ├── ARCHITECTURE.md          # Архитектура + диаграммы
│   └── ADDING_AGENTS.md         # Гайд по добавлению агентов
│
├── docker-compose.yml
├── docker-compose.test.yml
└── .github/workflows/
    ├── ci.yml                   # lint + test
    └── deploy-test.yml          # деплой на VPS
```

---

## GitHub Secrets

| Secret | Описание |
|--------|---------|
| `VPS_HOST` | IP тест-сервера |
| `VPS_USER` | Пользователь на сервере |
| `VPS_SSH_KEY` | SSH ключ для деплоя |
| `TEST_DB_PASSWORD` | Пароль PostgreSQL |
| `YC_API_KEY` | Yandex AI Studio API key |
| `YC_FOLDER_ID` | Yandex Cloud folder ID |
| `TRACKER_TOKEN` | Яндекс Трекер OAuth token |
| `TRACKER_ORG_ID` | ID организации Трекера |
| `TRACKER_ORG_TYPE` | `cloud` или `360` |
| `TRACKER_QUEUE` | Ключ очереди, напр. `DARKHORSE` |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram бота (для алертов) |
| `TELEGRAM_CHAT_ID` | ID чата для алертов |
| `GRAFANA_USER` / `GRAFANA_PASSWORD` | Логин Grafana |
