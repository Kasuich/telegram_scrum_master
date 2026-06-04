# Архитектура PM Agent Platform

## Обзор

PM Agent Platform — мультиагентная платформа для управления проектами поверх Яндекс Трекера. Агент понимает запросы на естественном языке, вызывает инструменты Трекера и запрашивает подтверждение у пользователя для рискованных операций.

---

## Компоненты системы

```
┌─────────────────────────────────────────────────────────────────┐
│                         Пользователь                            │
│                   (HTTP curl / Swagger UI)                       │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP :8000
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      platform-api                               │
│              Тонкий HTTP транспортный слой                      │
│   POST /chat   POST /confirm/{id}   GET /actions   GET /metrics │
└────────────────────────────┬────────────────────────────────────┘
                             │ JSON-RPC 2.0 POST /rpc
                             │ (in-process в dev, HTTP в Docker)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     pm-orchestrator :8001                       │
│                        Мозг системы                             │
│                                                                 │
│  ┌─────────────────┐   ┌────────────────────────────────────┐  │
│  │  OrchestratorService  │   │         agents/              │  │
│  │  invoke / resume      │   │   pm_agent.py  ← BaseAgent   │  │
│  │  confirm_index        │   │   my_agent.py  ← BaseAgent   │  │
│  └──────────┬────────────┘   │   (автодискавери при старте) │  │
│             │                └────────────────────────────────┘  │
│             ▼                                                   │
│  ┌─────────────────┐         ┌───────────────────────────────┐  │
│  │   ReActRunner   │         │       ToolRegistry            │  │
│  │  LLM → tool     │ ──────► │  @platform_tool decorator    │  │
│  │  → confirm_wait │         │  tracker_get_issue  (low)     │  │
│  │  → resume       │         │  tracker_create_issue (medium)│  │
│  └─────────────────┘         │  tracker_close_issue (high)   │  │
└────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Яндекс Трекер API v3                        │
│              REST https://api.tracker.yandex.net/v3/           │
└─────────────────────────────────────────────────────────────────┘
```

---

## Стек технологий

| Слой | Технология |
|------|-----------|
| LLM | gpt-oss-120b (Yandex Cloud OpenAI-совместимый Responses API `/v1/responses`), прямой HTTP |
| HTTP сервер | FastAPI + Uvicorn |
| Транспорт между сервисами | JSON-RPC 2.0 (HTTP в prod, in-process в dev) |
| Трекер | Yandex Tracker REST API v3 + httpx |
| Конфигурация | Pydantic Settings v2 + .env |
| ORM | SQLAlchemy 2.0 async + asyncpg |
| Мониторинг | Prometheus + Grafana + Alertmanager |
| Линтер | ruff |
| Пакетный менеджер | uv (workspace) |

---

## Поток запроса

```mermaid
sequenceDiagram
    participant U as Пользователь
    participant API as platform-api :8000
    participant ORC as pm-orchestrator :8001
    participant LLM as gpt-oss-120b
    participant TR as Яндекс Трекер

    U->>API: POST /chat {"message": "заведи задачу", "session_id": "s1"}
    API->>ORC: JSON-RPC invoke(agent="pm_agent", message, session_id)

    ORC->>LLM: messages + tool_schemas
    LLM-->>ORC: tool_call: tracker_create_issue(queue, summary)

    note over ORC: Autonomy Gate: medium risk → confirm!

    ORC-->>API: AgentResult{pending_confirm: {confirm_id, prompt}}
    API-->>U: 200 {pending_confirm: {confirm_id: "abc", prompt: "..."}}

    U->>API: POST /confirm/abc {"approved": true}
    API->>ORC: JSON-RPC resume(confirm_id="abc", approved=true)

    ORC->>TR: POST /issues/ {queue, summary}
    TR-->>ORC: {key: "DARKHORSE-42", ...}

    ORC->>LLM: "Инструмент выполнен. Результат: {key: DARKHORSE-42}. Сообщи."
    LLM-->>ORC: "Задача DARKHORSE-42 создана успешно!"

    ORC-->>API: AgentResult{reply: "Задача DARKHORSE-42 создана успешно!"}
    API-->>U: 200 {reply: "Задача DARKHORSE-42 создана успешно!"}
```

---

## ReAct цикл

```mermaid
flowchart TD
    START([invoke / resume]) --> MSG[Добавить сообщение в историю]
    MSG --> LLM[Вызов LLM с историей + tool_schemas]
    LLM --> CHECK{Тип ответа?}

    CHECK -- text --> REPLY([Вернуть AgentResult.reply])
    CHECK -- tool_call --> GATE{Autonomy Gate}

    GATE -- risk=low\nauto_risk → execute --> EXEC[Выполнить инструмент]
    GATE -- risk=medium/high\nconfirm_risk --> CONFIRM([Вернуть AgentResult.pending_confirm])

    EXEC --> RESULT[Добавить результат в историю]
    RESULT --> ITER{Лимит итераций < 8?}
    ITER -- да --> LLM
    ITER -- нет --> LIMIT([Ответ: лимит достигнут])
```

---

## Автодискавери агентов

```mermaid
flowchart LR
    FILE[agents/my_agent.py\nclass MyAgent(BaseAgent):] 
    DISC[OrchestratorService\n.discover_agents()]
    RUN[ReActRunner\nдля MyAgent]
    RPC[JSON-RPC метод\ninvoke(agent='my_agent')]
    HTTP[HTTP маршрут\nPOST /agents/my_agent/chat]

    FILE --> DISC --> RUN --> RPC --> HTTP
```

При старте оркестратор сканирует пакет `agents/`, импортирует все модули, находит подклассы `BaseAgent` и автоматически регистрирует их. Добавление нового агента = создать один файл.

---

## JSON-RPC 2.0 протокол

**Endpoint:** `POST http://pm-orchestrator:8001/rpc`

| Метод | Параметры | Ответ |
|-------|----------|-------|
| `list_agents` | — | `[{name, description}]` |
| `invoke` | `agent, message, session_id` | `AgentResult` |
| `resume` | `confirm_id, approved` | `AgentResult` |
| `get_actions` | `session_id?, limit?` | `[action]` |

```json
// Запрос
{
  "jsonrpc": "2.0",
  "method": "invoke",
  "params": {"agent": "pm_agent", "message": "найди задачи", "session_id": "s1"},
  "id": 1
}

// Ответ
{
  "jsonrpc": "2.0",
  "result": {"reply": "Найдено 3 задачи...", "session_id": "s1", "steps": [...]},
  "id": 1
}
```

**Режимы работы `rpc_client.py`:**
- `dev / тесты` — прямой Python-вызов (in-process, без HTTP)
- `Docker` — HTTP при наличии переменной `ORCHESTRATOR_URL`

---

## Autonomy Gate

Каждый инструмент имеет уровень риска. Перед выполнением оркестратор проверяет:

| Риск | Поведение | Примеры |
|------|----------|---------|
| `low` | Авто-выполнение | get_issue, search_issues, comment |
| `medium` | Пауза → confirm | create_issue, update_issue |
| `high` | Пауза → confirm | close_issue |

Настраивается через `RuntimeConfig(auto_risk=["low"], confirm_risk=["medium", "high"])`.  
Конкретные инструменты можно всегда требовать confirm через `always_confirm_tools`.

---

## Структура монорепо

```
digital_breakthrough_2026/
├── packages/
│   └── core/                    # Общая библиотека
│       └── src/core/
│           ├── agent.py         # BaseAgent, LLMSettings (default: gpt-oss-120b)
│           ├── react.py         # ReActRunner, AgentResult, _RunCtx (effective config)
│           ├── llm.py           # LLMClient → Responses API (gpt-oss-120b)
│           ├── tools.py         # @platform_tool, ToolRegistry
│           ├── effective_config.py  # build_effective_config (class < spec < overlay)
│           ├── scheduler.py     # SchedulerDaemon, compute_next_run
│           ├── seed.py          # ensure_default_team, ensure_agent_instances
│           ├── tracker.py       # TrackerClient
│           ├── tracker_tools.py # tracker_* @platform_tool
│           ├── config.py        # Pydantic settings (DEFAULT_TEAM_ID, SCHEDULER_ENABLED)
│           ├── metrics.py       # Prometheus counters/histograms
│           └── models.py        # SQLAlchemy ORM (11 таблиц)
│
├── services/
│   ├── pm-orchestrator/         # Мозг (порт 8001)
│   │   └── src/pm_orchestrator/
│   │       ├── agents/
│   │       │   └── pm_agent.py  # ← Добавить нового агента сюда
│   │       ├── tools/
│   │       │   ├── call_agent.py    # call_agent @platform_tool (делегирование)
│   │       │   └── schedule_task.py # schedule_task @platform_tool (cron-задачи)
│   │       ├── orchestrator.py  # OrchestratorService + effective config loading
│   │       └── rpc.py           # JSON-RPC сервер + SchedulerDaemon lifecycle
│   │
│   └── platform-api/            # HTTP транспорт (порт 8000)
│       └── src/platform_api/
│           ├── main.py          # FastAPI роуты
│           └── rpc_client.py    # JSON-RPC клиент
│
├── monitoring/                  # Prometheus + Grafana + Alertmanager
├── docker-compose.yml
└── .github/workflows/           # CI (lint + test) + CD (deploy to VPS)
```

---

## Встроенные инструменты оркестратора

| Инструмент | Risk | Описание |
|------------|------|----------|
| `call_agent` | low | Делегировать задачу другому агенту in-process |
| `schedule_task` | medium | Запланировать cron-задачу для агента |
| `tracker_*` (6 шт.) | low/medium/high | Яндекс Трекер CRUD |

## Effective Config (класс < spec < overlay)

Промпт и пороги автономии можно менять **без деплоя** через таблицы `agent_specs` и `agent_instances.overlay`:

```
class defaults → AgentSpec (prompt, model) → AgentInstance.overlay (prompt, autonomy thresholds)
```

Функция `build_effective_config(agent, spec_data, overlay_data)` возвращает `EffectiveAgentConfig` с итоговыми `prompt`, `llm_configs`, `runtime_config`.

## Scheduler daemon

`SchedulerDaemon` (asyncio-таск в `pm-orchestrator`) каждые 60 сек обрабатывает просроченные `ScheduledJob` через `SELECT … FOR UPDATE SKIP LOCKED` — безопасно для нескольких реплик.

---

## Мониторинг

- **Prometheus** — scrape с platform-api `/metrics` и pm-orchestrator `/metrics`
- **Grafana** — 3 дашборда: хост, контейнеры, приложение (LLM/tool метрики)
- **Alertmanager** → Telegram: контейнер упал, диск >80%, LLM latency p95 >15s

---

## Что отложено

- Networked A2A (агент→агент через сеть)
- Telegram-бот (Трек A)
- Meeting Capture агент
- Correspondence/Analytics агенты
- GUI-консоль (Трек D)
