# PM Agent Platform

Мультиагентная платформа для автоматизации работы Project Manager.
Агент понимает запросы на естественном языке, работает с Яндекс Трекером и запрашивает подтверждение перед рискованными действиями.

**Документация:**
- [Архитектура системы](docs/ARCHITECTURE.md)
- [Целевая архитектура и roadmap](docs/TARGET_ARCHITECTURE.md)
- [Как добавить нового агента](docs/ADDING_AGENTS.md)
- [Трек B — задачи рантайма](docs/TRACK_B_TASKS.md)

---

## Что умеет прямо сейчас

| Возможность | Статус |
|-------------|--------|
| ReAct-цикл (LLM → tool → confirm → resume) | ✅ |
| Autonomy Gate (low=авто, medium/high=confirm) | ✅ |
| Яндекс Трекер (get/search/create/update/comment/close) | ✅ |
| DB-персист (actions/traces/confirms) | ✅ |
| call_agent (делегирование агент→агент) | ✅ |
| schedule_task (cron-задачи через агента) | ✅ |
| Effective Config (промпт/пороги без деплоя) | ✅ |
| Scheduler daemon (SKIP LOCKED, мульти-реплика) | ✅ |
| Мониторинг (Prometheus + Grafana + Alertmanager) | ✅ |
| CI/CD → тест-VPS | ✅ |
| LLM: gpt-oss-120b (Yandex Responses API) | ✅ |
| Telegram-бот | 🔴 Трек A |
| Meeting Summarizer | 🔴 Трек C |
| GUI-консоль | 🔴 Трек D |

---

## Быстрый старт

### Что нужно

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — менеджер зависимостей
- Docker + Docker Compose
- Yandex Cloud: API key + folder ID
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
cp .env.example .env.test
```

Заполнить в `.env.test`:

```env
# Yandex Cloud (LLM — gpt-oss-120b via Responses API)
YC_API_KEY=ваш_ключ
YC_FOLDER_ID=b1g...

# Яндекс Трекер
TRACKER_TOKEN=ваш_oauth_token
TRACKER_ORG_ID=ваш_org_id
TRACKER_ORG_TYPE=cloud          # или 360 для Яндекс 360
TRACKER_QUEUE=DARKHORSE         # ключ вашей очереди

# БД
DB_USER=pm_agent
DB_PASSWORD=changeme
DB_NAME=pm_agent

# Команда по умолчанию (для DB-персиста)
DEFAULT_TEAM_ID=00000000-0000-0000-0000-000000000001

ENVIRONMENT=development
LOG_LEVEL=DEBUG
```

### 3. Запустить в Docker

```bash
# Приложение + Postgres
docker compose -f docker-compose.yml -f docker-compose.test.yml --env-file .env.test up --build

# С мониторингом
docker compose -f docker-compose.yml -f docker-compose.test.yml -f docker-compose.monitoring.yml \
  --env-file .env.test up --build
```

Сервисы:

| Сервис | URL |
|--------|-----|
| Platform API | http://localhost:8000/docs |
| PM Orchestrator | http://localhost:8001 |
| Meeting Capture | http://localhost:8003/health |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |

### 4. Запустить локально (без Docker)

```bash
# Терминал 1 — Postgres (или свой)
docker run -e POSTGRES_PASSWORD=pg -e POSTGRES_DB=pm_agent -p 5432:5432 postgres:16-alpine

# Терминал 2 — оркестратор (порт 8001)
uv run --package pm-orchestrator uvicorn pm_orchestrator.rpc:app --reload --port 8001

# Терминал 3 — HTTP API (порт 8000)
uv run --package platform-api uvicorn platform_api.main:app --reload --port 8000
```

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
# 1. Агент возвращает pending_confirm
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "заведи задачу: починить логин, приоритет critical", "session_id": "demo"}'
# → {"pending_confirm": {"confirm_id": "abc123", "prompt": "...", ...}}

# 2. Подтверждаем
curl -X POST http://localhost:8000/confirm/abc123 \
  -H "Content-Type: application/json" \
  -d '{"approved": true}'
```

### Делегирование между агентами (call_agent)

```bash
# PM Orchestrator вызывает Meeting Summarizer через call_agent
curl -X POST http://localhost:8000/chat \
  -d '{"message": "обработай транскрипт: <текст>", "session_id": "s1"}'
```

### Запланировать задачу (schedule_task)

```bash
# Агент сам планирует cron-задачу (risk=medium → confirm)
curl -X POST http://localhost:8000/chat \
  -d '{"message": "напоминай мне каждый понедельник в 9:00 про стендап", "session_id": "s2"}'
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

Создать один файл — оркестратор подхватит автоматически:

```python
# services/pm-orchestrator/src/pm_orchestrator/agents/my_agent.py
from core.agent import BaseAgent, LLMSettings

class MyAgent(BaseAgent):
    name = "my_agent"
    description = "Что делает агент"
    prompt = "Ты — агент для ..."
    tools = ["tracker_get_issue", "call_agent"]   # включая call_agent
    llm_configs = [LLMSettings(model="gpt-oss-120b", temperature=0.3)]
```

Перезапустить оркестратор — агент доступен через `POST /agents/my_agent/chat`.

Подробный гайд: [docs/ADDING_AGENTS.md](docs/ADDING_AGENTS.md)

---

## Effective Config (промпт без деплоя)

Промпт и пороги автономии агента можно менять через таблицы `agent_specs` и `agent_instances.overlay` без перезапуска:

```
class defaults → AgentSpec (prompt, model) → AgentInstance.overlay (team-специфичные overrides)
```

Пример overlay в `agent_instances`:
```json
{
  "prompt": "Ты — строгий PM-ассистент. Никогда не создавай задачи без явного priority.",
  "auto_risk": ["low"],
  "confirm_risk": ["medium", "high"],
  "always_confirm_tools": ["tracker_close_issue"]
}
```

---

## Разработка

### Тесты

```bash
# Unit + сервисные (CI-команда)
uv run pytest packages/core/tests/unit services/ -q

# Smoke-тесты (нужны реальные ключи)
cd packages/core && uv run pytest tests/smoke/ -m smoke -v

# С DB round-trip (нужен реальный Postgres)
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/test \
  uv run pytest packages/core/tests/unit/test_react_persist.py -v
```

### Линтер и форматирование

```bash
uvx ruff check .           # проверить
uvx ruff check --fix .     # автофикс
uvx ruff format .          # форматировать
```

### Добавить зависимость

```bash
uv add --package pm-orchestrator croniter
uv add --package platform-api aiogram
```

---

## Деплой на тест-сервер

```
feature/* → PR → develop → GitHub Actions → тест-сервер (автоматически)
```

**CI** (при каждом push/PR в develop/main):
- `ruff check .` + `ruff format --check .`
- `pytest packages/core/tests/unit services/`

**CD** (при merge в develop):
- rsync кода на VPS
- `docker compose up --build` (app + monitoring)

### Статус деплоя

```bash
gh run list --limit 5
gh pr list
```

### Логи на сервере

```bash
docker logs test-pm-orchestrator-1 -f
docker logs test-platform-api-1 -f
docker logs test-grafana-1 -f
```

---

## Структура проекта

```
digital_breakthrough_2026/
│
├── packages/core/               # Общая библиотека
│   └── src/core/
│       ├── agent.py             # BaseAgent, LLMSettings
│       ├── react.py             # ReActRunner + _RunCtx (effective config)
│       ├── llm.py               # LLMClient → Responses API (gpt-oss-120b)
│       ├── tools.py             # @platform_tool, ToolRegistry
│       ├── effective_config.py  # build_effective_config (class < spec < overlay)
│       ├── scheduler.py         # SchedulerDaemon, compute_next_run
│       ├── seed.py              # ensure_default_team, ensure_agent_instances
│       ├── tracker.py           # Яндекс Трекер клиент
│       ├── tracker_tools.py     # tracker_* инструменты
│       ├── metrics.py           # Prometheus counters/histograms
│       └── models.py            # SQLAlchemy ORM (11 таблиц)
│
├── services/
│   ├── pm-orchestrator/ :8001   # Мозг: агенты + JSON-RPC + Scheduler
│   │   └── src/pm_orchestrator/
│   │       ├── agents/          # ← Новый агент = один файл здесь
│   │       │   └── pm_agent.py
│   │       ├── tools/
│   │       │   ├── call_agent.py    # делегирование агент→агент
│   │       │   └── schedule_task.py # cron-задачи
│   │       ├── orchestrator.py  # OrchestratorService + effective config
│   │       └── rpc.py           # JSON-RPC 2.0 + SchedulerDaemon lifecycle
│   │
│   └── platform-api/ :8000      # Тонкий HTTP транспорт
│       └── src/platform_api/
│           ├── main.py          # FastAPI роуты
│           └── rpc_client.py    # Клиент оркестратора
│
├── monitoring/                  # Prometheus + Grafana + Alertmanager
│   ├── prometheus.yml
│   ├── alerts.yml
│   ├── alertmanager.yml
│   └── grafana/                 # Provisioning + 3 дашборда
│
├── docs/
│   ├── ARCHITECTURE.md          # Архитектура + диаграммы
│   ├── TARGET_ARCHITECTURE.md   # Roadmap + статусы
│   ├── ADDING_AGENTS.md         # Гайд по добавлению агентов
│   └── TRACK_B_TASKS.md         # Детальные задачи Трека B
│
├── docker-compose.yml
├── docker-compose.test.yml
├── docker-compose.monitoring.yml
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
| `YC_API_KEY` | Yandex Cloud API key |
| `YC_FOLDER_ID` | Yandex Cloud folder ID |
| `TRACKER_TOKEN` | Яндекс Трекер OAuth token |
| `TRACKER_ORG_ID` | ID организации Трекера |
| `TRACKER_ORG_TYPE` | `cloud` или `360` |
| `TRACKER_QUEUE` | Ключ очереди, напр. `DARKHORSE` |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram бота (для алертов Alertmanager) |
| `TELEGRAM_CHAT_ID` | ID чата для алертов |
| `GRAFANA_USER` / `GRAFANA_PASSWORD` | Логин Grafana |
