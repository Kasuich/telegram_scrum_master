# PM Agent Platform — Полное описание платформы

> Мульти-агентная платформа для автоматизации работы Project Manager.
> Агенты ведут канбан-доску, делают саммари встреч, анализируют переписку,
> присылают алерты — и всё это с контролируемым уровнем автономии.

---

## Содержание

1. [Зачем это нужно](#зачем-это-нужно)
2. [Как устроена платформа](#как-устроена-платформа)
3. [Агенты и тулзы](#агенты-и-тулзы)
4. [Как агент принимает решения — Autonomy Gate](#как-агент-принимает-решения--autonomy-gate)
5. [A2A — как агенты общаются между собой](#a2a--как-агенты-общаются-между-собой)
6. [Создание агентов — Code plane vs Control plane](#создание-агентов--code-plane-vs-control-plane)
7. [Роли и права](#роли-и-права)
8. [Интерфейсы — три личных кабинета](#интерфейсы--три-личных-кабинета)
9. [Данные и модель хранения](#данные-и-модель-хранения)
10. [Roadmap — фазы разработки](#roadmap--фазы-разработки)
11. [Тестирование на команде](#тестирование-на-команде)

**Смежные документы:**
- [Meeting Capture — как добываются транскрипты встреч](meeting_capture.md)
- [Оценка качества агента — как получать цифры для сравнения подходов](agent_evaluation.md)

---

## Зачем это нужно

PM тратит значительную часть времени на рутину: обновить доску после встречи, разнести action items, напомнить о дедлайне, запросить статус у разработчика. Всё это — детерминированные или полудетерминированные задачи, которые можно автоматизировать.

**Что делает агент:**
- Ходит на встречи (транскрипт) → делает саммари → заводит задачи в Трекере
- Читает переписку → находит изменения требований, запрашивает вводные
- Ведёт канбан-доску: создаёт, двигает, комментирует задачи
- Присылает алерты: застрявшая задача, приближающийся дедлайн, SLA под угрозой
- Перед рискованными действиями — **спрашивает подтверждение у PM**

**Чего агент не делает без PM:**
- Не удаляет задачи без подтверждения
- Не переназначает ресурсы сам
- Не эскалирует вовне без согласования

Уровень автономии настраивается per-команда и меняется без деплоя.

---

## Как устроена платформа

### Общая архитектура

```mermaid
graph TB
    subgraph inputs["Источники входа"]
        CHAT["💬 Чат (TG / Мессенджер)"]
        MEET["🎙️ Транскрипт встречи"]
        MAIL["📨 Переписка"]
        CRON["⏰ Cron / очередь"]
    end

    subgraph platform["Платформа"]
        direction TB
        ORCH["🧠 PM Orchestrator\n(per-team)"]
        GATE["🔒 Autonomy Gate"]
        
        subgraph specialists["Специалисты (shared)"]
            MS["📝 Meeting Summarizer"]
            CA["📊 Correspondence Analyzer"]
            AA["📈 Analytics Agent"]
        end
        
        subgraph tools["Тулзы"]
            TR["Яндекс Трекер\nCRUD + метрики"]
            AL["alert / reminder\nconfirm / request_input"]
            SC["schedule_task\n(self-scheduling)"]
        end
        
        REG["📋 Agent Registry\n(A2A discovery)"]
        LLM["⚡ LiteLLM\n→ Yandex AI Studio"]
        DB[("🐘 PostgreSQL\nall-in-one")]
    end

    subgraph ui["Интерфейсы"]
        DEV_UI["🛠️ Dev UI\nАгенты, трейсы, токены"]
        ADMIN_UI["👔 Admin / PM UI\nДашборд команды, confirm"]
        USER_UI["👤 User UI\nМои задачи, фидбек"]
    end

    inputs --> ORCH
    ORCH --> GATE
    GATE -->|"риск medium+"| CHAT
    GATE -->|"риск low"| TR
    ORCH -->|A2A| MS
    ORCH -->|A2A| CA
    ORCH -->|A2A| AA
    ORCH --> AL
    ORCH --> SC
    MS & CA & AA --> LLM
    ORCH --> LLM
    LLM --> DB
    GATE --> DB
    REG --- ORCH
    DB --> DEV_UI & ADMIN_UI & USER_UI
```

### Технический стек

| Слой | Выбор | Почему |
|---|---|---|
| **Агенты** | LangGraph (ReAct + StateGraph) | Встроенный checkpointer, `interrupt()` для human-in-loop, граф состояний |
| **A2A** | a2a-sdk (JSON-RPC, SSE) | Открытый стандарт, агенты общаются как сервисы |
| **LLM** | LiteLLM → Yandex AI Studio | Один интерфейс ко всем провайдерам, fallback-цепочки, retry, бюджеты |
| **API** | FastAPI + uvicorn | Async, webhook'и, OpenAPI из коробки |
| **Cron** | APScheduler (in-process) | Persistent jobs в Postgres без отдельного брокера |
| **Очередь** | Postgres `FOR UPDATE SKIP LOCKED` | Worker-pool без Redis/RabbitMQ на старте |
| **БД** | PostgreSQL | Одна БД на всё: задачи, трейсы, чекпоинты, конфиги, метрики |
| **Монорепо** | uv workspaces | `packages/core` + `services/*`, общие зависимости |
| **Деплой** | Docker Compose | Каждый агент — свой образ |

### Структура монорепо

```
agent-platform/
├── packages/
│   └── core/                    # Общая библиотека
│       └── src/core/
│           ├── agent/           # BaseAgent, ReAct presets
│           ├── a2a/             # server, client, remote-tool
│           ├── llm.py           # LiteLLM wrapper + fallbacks
│           ├── db.py            # asyncpg pool + checkpointer
│           └── config.py        # Pydantic Settings + runtime_configs
│
├── services/
│   ├── platform-api/            # FastAPI: REST + A2A endpoint, Registry
│   ├── pm-orchestrator/         # PM Orchestrator (per-team brain)
│   ├── meeting-summarizer/      # Транскрипт → action items
│   ├── correspondence-analyzer/ # Переписка → изменения/вводные
│   └── analytics-agent/         # Метрики → выводы + предложения
│
├── migrations/                  # Alembic, одна БД
└── docker-compose.yml
```

Сервисы импортируют `core`, но **не друг друга** — только через A2A по сети.

---

## Агенты и тулзы

### Четыре агента

```mermaid
graph LR
    subgraph orchestrator["🧠 PM Orchestrator  (per-team)"]
        O_DESC["Мозг системы. Принимает все входы,\nрассуждает, делегирует специалистам,\nведёт канбан-доску"]
    end

    subgraph meeting["📝 Meeting Summarizer  (shared)"]
        M_DESC["Вход: транскрипт встречи\nВыход: саммари + action items\n+ черновик задач для Трекера"]
    end

    subgraph corr["📨 Correspondence Analyzer  (shared)"]
        C_DESC["Вход: тред переписки\nВыход: изменения требований,\nзапросы вводных, эскалации"]
    end

    subgraph analytics["📈 Analytics Agent  (shared)"]
        A_DESC["Вход: метрики (SLA, burndown)\nВыход: выводы + конкретные\nпредложения решений"]
    end

    orchestrator -->|A2A| meeting
    orchestrator -->|A2A| corr
    orchestrator -->|A2A| analytics
```

Все четыре агента — **чистый конфиг**, без кастомного кода. Поведение меняется правкой промпта в дашборде без деплоя.

> Транскрипт для Meeting Summarizer добывает отдельная детерминированная подсистема (бот заходит на встречу, пишет аудио, расшифровывает) — см. [Meeting Capture](meeting_capture.md).

### Тулзы

**Яндекс Трекер** — CRUD-интеграция:

| Тул | Что делает | Риск |
|---|---|---|
| `tracker_create_issue` | Создать задачу в очереди | medium |
| `tracker_update_issue` | Обновить поля задачи | low |
| `tracker_move_issue` | Перевести по статусу (In Progress → Done) | low |
| `tracker_comment` | Добавить комментарий | low |
| `tracker_link_issues` | Связать задачи (блокирует/дублирует) | low |
| `tracker_get_issue` | Прочитать задачу | — |
| `tracker_search` | Поиск по фильтрам | — |
| `tracker_get_sprint` | Данные спринта | — |
| `tracker_get_metrics` | Story points, burndown, SLA | — |

**Коммуникация:**

| Тул | Что делает |
|---|---|
| `alert` | Уведомление в чат (не требует ответа) |
| `reminder` | Отложенное напоминание |
| `confirm` | Запрос подтверждения (interrupt — ждёт ответа PM) |
| `request_input` | Запрос вводных у члена команды |

**Платформенные:**

| Тул | Что делает |
|---|---|
| `call_agent:X` | Вызов другого агента по A2A (авто-генерится из Registry) |
| `schedule_task` | Агент сам ставит cron/one-off задачи |

### Как 5 ключевых потоков ложатся на агентов

```mermaid
sequenceDiagram
    participant Human as 👤 PM / Участник
    participant Orch as 🧠 Orchestrator
    participant MS as 📝 Meeting Summarizer
    participant Tracker as 📋 Яндекс Трекер

    Note over Human,Tracker: Поток 1 — встреча
    Human->>Orch: транскрипт встречи
    Orch->>MS: A2A: summarize(transcript)
    MS-->>Orch: action_items[]
    Orch->>Human: confirm: "Создать 3 задачи?"
    Human-->>Orch: ✅ да
    Orch->>Tracker: tracker_create_issue × 3

    Note over Human,Tracker: Поток 4 — алерт о дедлайне
    Note over Orch: cron tick
    Orch->>Tracker: tracker_search(deadline < 2d, status != Done)
    Tracker-->>Orch: задачи X, Y
    Orch->>Human: alert: "Задача X горит, дедлайн завтра"

    Note over Human,Tracker: Поток 5 — urgent confirm
    Orch->>Human: confirm: "Переназначить X → Иванову?"
    Human-->>Orch: ✅ да
    Orch->>Tracker: tracker_update_issue(assignee=Иванов)
```

---

## Как агент принимает решения — Autonomy Gate

Платформа реализует **автономию уровня 2**: рутина выполняется автоматически, рискованные действия идут на подтверждение к PM.

```mermaid
flowchart TD
    A["🤖 Агент хочет вызвать tool"] --> B{Autonomy Gate}
    
    B --> C{"risk = low\nИ не в always_confirm?"}
    C -->|да| D["✅ Выполнить сразу"]
    D --> E["📢 Уведомить PM\n(alert, не блокирует)"]
    
    C -->|нет| F["⏸️ interrupt()\nОтправить confirm в чат"]
    F --> G{Ответ PM}
    G -->|"✅ Да"| H["✅ Выполнить"]
    G -->|"❌ Нет / правка"| I["🔄 Вернуть фидбек агенту\nАгент пересматривает"]
    
    style B fill:#f4a460,color:#000
    style D fill:#90EE90,color:#000
    style F fill:#FFD700,color:#000
    style H fill:#90EE90,color:#000
    style I fill:#FFB6C1,color:#000
```

**Уровни риска тулов:**

| Риск | Примеры | Поведение по умолчанию |
|---|---|---|
| `low` | update_issue, comment, get_* | Авто, уведомление |
| `medium` | create_issue, move_issue | Confirm |
| `high` | delete_issue, reassign, массовые операции | Всегда confirm |

**Конфигурация в `runtime_configs`** — меняется без деплоя, per-команда:
```yaml
# Тестовая команда (всё через confirm пока не доверяем)
autonomy:
  auto_risk: []
  confirm_risk: [low, medium, high]

# Зрелая команда
autonomy:
  auto_risk: [low]
  confirm_risk: [medium, high]
  always_confirm_tools: [tracker_delete_issue, tracker_reassign]
```

---

## A2A — как агенты общаются между собой

A2A (Agent-to-Agent) — открытый протокол для взаимодействия агентов как сервисов.

### Принцип: удалённый агент = tool

Orchestrator видит `call_agent:meeting_summarizer` в списке инструментов и не знает, что под капотом — HTTP-вызов. Это позволяет начать с in-process вызовов и вынести агента в отдельный сервис без изменения кода вызывающего.

```mermaid
sequenceDiagram
    participant LLM as ⚡ LLM
    participant OA2A as 🤖 Orchestrator A2A Client
    participant REG as 📋 Registry
    participant MS as 📝 Meeting Summarizer

    LLM->>OA2A: tool_call: call_agent:meeting_summarizer
    OA2A->>REG: lookup endpoint
    REG-->>OA2A: http://meeting-summarizer:8001/a2a
    OA2A->>MS: POST /a2a {message, call_chain:["orchestrator"], depth:1}
    MS-->>OA2A: {task_id: "abc123"}
    loop polling / SSE
        OA2A->>MS: GET /a2a/tasks/abc123
        MS-->>OA2A: {status: "working"}
    end
    MS-->>OA2A: {status: "completed", result: {...}}
    OA2A-->>LLM: результат
```

### Защиты от проблем

| Проблема | Решение |
|---|---|
| Цикл A → B → A | `call_chain` + `max_depth` в каждом запросе |
| Долгие задачи | task-модель (submitted/working/completed), не синхронный HTTP |
| B недоступен | timeout на tool + понятная ошибка агенту A |
| Auth | service-token (JWT) + ACL в Agent Card |
| Наблюдаемость | сквозной `trace_id` через весь call_chain |

### Agent Card — визитка агента

Каждый агент публикует `/.well-known/agent-card.json`:
```json
{
  "id": "meeting_summarizer",
  "name": "Meeting Summarizer",
  "endpoint": "http://meeting-summarizer:8001/a2a",
  "capabilities": ["summarize_meeting", "extract_action_items"],
  "acl": ["orchestrator"]
}
```

---

## Создание агентов — Code plane vs Control plane

```mermaid
graph LR
    subgraph code["⚙️ Code plane (деплой нужен)"]
        T1["Написать tool-функцию\n+ @platform_tool декоратор"]
        T2["Кастомный LangGraph граф\n(только для сложных агентов)"]
    end

    subgraph control["🎛️ Control plane (без деплоя)"]
        C1["AgentSpec в БД:\nпромпт, модель, tools[], autonomy"]
        C2["Team overlay PM'а:\nкоманд-специфичные инструкции,\nпороги confirm"]
    end

    subgraph result["✨ Результат"]
        R1["Новый агент\nбез кода"]
        R2["Промпт / модель\nменяется в дашборде"]
        R3["Новый тул — деплой,\nно агент получает его\nбез деплоя"]
    end

    code --> result
    control --> result
```

### Пример: объявление тула

```python
@platform_tool(name="tracker_create_issue", scopes=["tracker:write"], risk="medium")
async def tracker_create_issue(
    queue: str,
    summary: str,
    description: str = "",
    assignee: str | None = None,
) -> dict:
    """Создать задачу в Яндекс Трекере."""
    return await tracker_client.create(queue=queue, summary=summary, ...)
```

При импорте — автоматически в `ToolRegistry`. Агентам добавляется через конфиг в дашборде.

### Пример: объявление агента (конфиг, без кода)

```yaml
id: pm_orchestrator
model: yandexgpt-pro
prompt: |
  Ты PM-ассистент команды. Ведёшь доску в Яндекс Трекере.
  Очередь команды: MYTEAM. Спринты — 2 недели.
  Перед созданием задач — уточни приоритет у PM.
tools:
  - tracker_create_issue
  - tracker_update_issue
  - tracker_move_issue
  - tracker_comment
  - tracker_search
  - alert
  - confirm
  - call_agent:meeting_summarizer
  - call_agent:analytics_agent
autonomy:
  auto_risk: [low]
  confirm_risk: [medium, high]
  always_confirm_tools: [tracker_delete_issue]
```

---

## Роли и права

```mermaid
graph TD
    ORG["🏢 Organization"]
    
    ORG --> PA["🔑 Platform Admin\nscope: org\n\nВся платформа, все команды,\nглобальный kill-switch"]
    ORG --> DEV["🛠️ Agent Developer\nscope: org\n\nАгенты, тулзы, тех-трейсы,\nтокены, A2A"]
    
    ORG --> T1["👥 Team A"]
    ORG --> T2["👥 Team B"]
    ORG --> TN["👥 Team N"]
    
    T1 --> PM1["👔 PM / Team Admin\nscope: team\n\nАвтономия команды, confirm,\noverlay агента, kill-switch команды"]
    T1 --> M1["👤 Team Member\nscope: team\n\nСвои задачи, фидбек,\nгеймификация"]
    
    style PA fill:#FF6B6B,color:#fff
    style DEV fill:#4ECDC4,color:#fff
    style PM1 fill:#45B7D1,color:#fff
    style M1 fill:#96CEB4,color:#fff
```

### RBAC-матрица

| Возможность | Member | PM | Developer | Platform Admin |
|---|---|---|---|---|
| Свои задачи + фидбек | ✅ | ✅ | ✅ | ✅ |
| Дашборд команды / бизнес-трейсы | своя | свои | — | все |
| Подтверждение urgent-действий | — | свои | — | все |
| Настройка автономии (runtime_configs) | — | свои | ✅ | ✅ |
| Overlay агента (team-инструкции) | — | свои | ✅ | ✅ |
| AgentSpec: промпт, модель, tools | — | — | ✅ | ✅ |
| Тех-трейсы, токены, ошибки | — | агрегат | ✅ | ✅ |
| Управление тулзами (code) | — | — | ✅ | ✅ |
| Создание/удаление команд | — | — | — | ✅ |
| Глобальный kill-switch | — | команда | — | ✅ |

### Layered config — PM не сломает агента

```
AgentSpec (template)     ← Developer: базовый промпт, список tools, модель, guardrails
         +
Team overlay             ← PM: «у нас спринты 2 недели», пороги confirm
         =
Effective config         ← то, с чем работает инстанс команды
```

PM настраивает поведение **в рамках разрешённого** — не трогает архитектуру.

---

## Интерфейсы — три личных кабинета

```mermaid
graph LR
    subgraph data["Данные (один источник)"]
        A["actions +\ncheckpoints"]
        B["OTel traces"]
        C["runtime_configs"]
        D["LiteLLM usage"]
        E["Трекер\nread-модель"]
        F["action_feedback"]
    end

    subgraph user_ui["👤 Личный кабинет\nчлена команды"]
        U1["Мои задачи\n(подсветка: заведено агентом)"]
        U2["Лента: что агент\nсделал по мне"]
        U3["Запросы вводных\nот агента"]
        U4["Лидерборд /\nачивки"]
        U5["Фидбек ⭐"]
    end

    subgraph admin_ui["👔 Кабинет PM / Admin"]
        P1["Дашборд команды\nburndown, SLA, риски"]
        P2["Лента действий\n(бизнес-трейс 'почему')"]
        P3["Очередь confirm"]
        P4["Метрики агента\nacceptance rate"]
        P5["Управление\nавтономией"]
        P6["Kill-switch 🔴"]
    end

    subgraph dev_ui["🛠️ Кабинет разработчика"]
        D1["Реестр агентов\n+ редактор промпта"]
        D2["Playground"]
        D3["Тех-трейсы\n(шаги графа, tool I/O)"]
        D4["Токены / стоимость"]
        D5["A2A карта"]
        D6["Scheduled jobs"]
    end

    A --> U2 & P2 & D3
    B --> D3
    C --> P5 & D1
    D --> P1 & D4
    E --> U1 & P1
    F --> U5 & P4 & D1
```

---

## Данные и модель хранения

Одна PostgreSQL на всё — без лишних сервисов на старте.

```mermaid
erDiagram
    organizations ||--o{ teams : has
    teams ||--o{ users : members
    teams ||--|| agent_instances : has
    agent_specs ||--o{ agent_instances : template

    agent_instances ||--o{ actions : performs
    actions ||--o{ action_feedback : receives
    actions }o--|| traces : linked_to

    teams ||--o{ runtime_configs : configures
    agent_instances ||--o{ scheduled_jobs : creates

    organizations {
        uuid id
        string name
    }
    teams {
        uuid id
        string name
        string tracker_queue
        uuid agent_instance_id
    }
    agent_specs {
        uuid id
        string model
        text prompt
        jsonb tools
        jsonb autonomy
    }
    agent_instances {
        uuid id
        uuid team_id
        uuid template_spec_id
        jsonb overlay
        bool enabled
    }
    actions {
        uuid id
        uuid team_id
        string tool_name
        jsonb input
        jsonb output
        string risk_level
        string status
        uuid trace_id
        timestamptz created_at
    }
    action_feedback {
        uuid id
        uuid action_id
        int rating
        text comment
    }
    scheduled_jobs {
        uuid id
        uuid agent_instance_id
        string cron_expr
        jsonb payload
        int max_runs
        timestamptz next_run
    }
    runtime_configs {
        uuid id
        uuid team_id
        string key
        jsonb value
    }
```

---

## Roadmap — фазы разработки

```mermaid
gantt
    title Roadmap PM Agent Platform
    dateFormat  YYYY-MM-DD
    axisFormat  %b %Y

    section Фаза 0 — Скелет
    platform_core + Tracker client       :done, p0a, 2026-06-01, 14d
    1 агент отвечает по HTTP              :done, p0b, after p0a, 7d

    section Фаза 1 — Shadow
    Meeting Summarizer (read-only)        :p1a, after p0b, 14d
    Черновики задач без записи в доску    :p1b, after p1a, 7d

    section Фаза 2 — Confirm-all
    Запись в Трекер через confirm         :p2a, after p1b, 14d
    Correspondence Analyzer               :p2b, after p2a, 14d

    section Фаза 3 — Autonomy L2
    Autonomy Gate: рутина авто            :p3a, after p2b, 14d
    runtime_configs per-team              :p3b, after p3a, 7d

    section Фаза 4 — Алерты
    Cron: дедлайны, SLA                   :p4a, after p3b, 14d
    Analytics Agent                       :p4b, after p4a, 14d

    section Фаза 5 — Дашборд
    Dev UI: агенты, трейсы                :p5a, after p4b, 21d
    Admin UI: дашборд, confirm, kill      :p5b, after p5a, 14d
    User UI: задачи, фидбек               :p5c, after p5b, 14d
```

| Фаза | Что делаем | Критерий готовности |
|---|---|---|
| **0. Скелет** | `platform_core` + Tracker-client + 1 агент + docker-compose | Агент отвечает «что в задаче X» по HTTP |
| **1. Shadow** | Meeting Summarizer, агент строит черновик — НЕ пишет в доску | PM оценил качество саммари/action items |
| **2. Confirm-all** | Запись в Трекер, каждое действие через confirm + Correspondence Analyzer | Агент реально ведёт доску, PM подтверждает каждый шаг |
| **3. Level 2** | Autonomy Gate: рутина авто, риск через confirm, `runtime_configs` | PM подтверждает только важное |
| **4. Алерты** | Cron: дедлайны, застрявшие задачи, SLA + Analytics Agent | Проактивные алерты, предложения решений |
| **5. Дашборд** | Три UI: Dev + Admin + User | PM/команда видят, оценивают, управляют |

---

## Тестирование на команде

### Принцип: от наблюдения к автономии

```mermaid
graph LR
    S["👁️ Фаза 1\nShadow\n\nАгент молча строит черновики\nPM ведёт доску сам\n\nМеряем: точность саммари"] 
    --> C["✋ Фаза 2\nConfirm-all\n\nКаждое действие — confirm\nPM жмёт да/нет\n\nМеряем: acceptance rate"]
    --> A["🤖 Фаза 3\nLevel 2\n\nРутина авто, риск — confirm\nPM подтверждает только важное\n\nМеряем: сэкономленное время"]
    --> P["📊 Фаза 4+\nПроактив\n\nАлерты, предложения\nАналитика команды\n\nМеряем: полезность алертов"]
```

### Ключевые метрики

| Метрика | Описание | Где смотреть |
|---|---|---|
| **Acceptance rate** | % принятых confirm-запросов | Admin UI → метрики агента |
| **Точность action items** | % корректных задач со встречи (shadow) | Ручная оценка PM |
| **Время от события до доски** | Встреча → задача в Трекере | actions.created_at |
| **Доля авто-действий** | % low-risk без confirm | runtime_configs stats |
| **Средняя оценка** | Фидбек ⭐ от команды | action_feedback |
| **Token cost / задача** | Стоимость LLM на одно действие | LiteLLM usage |

### Безопасность на тестовой команде

- **Отдельная очередь в Трекере** — не прод-доска
- **Kill-switch:** `agent.enabled = false` выключает агента мгновенно
- **Низкие пороги автономии** на старте — почти всё через confirm
- **Все действия логируются** с `trace_id` → разбор в дашборде

### Цикл улучшения

```
Низкая оценка фидбека / отказ на confirm
          ↓
Смотрим trace в Dev UI (шаги графа, промпт, решение LLM)
          ↓
Правим промпт в AgentSpec (без деплоя)
          ↓
Проверяем acceptance rate на следующей неделе
```
