# Целевая архитектура PM Agent Platform

> Стратегический документ: где мы сейчас, куда движемся, и почему.
> Объединяет видение из `discovery/` с реальным состоянием кода на 2026-06-03.

---

## 1. Что это и зачем (ценность)

**Проблема.** Работа Project Manager — это конвейер рутины: послушал встречу → завёл задачи, прочитал переписку → обновил доску, проверил дедлайны → напомнил. 60-70% этого — механическая работа, которая съедает время, но не требует уникального человеческого суждения.

**Решение.** Агент берёт на себя черновую работу конвейера. Человек остаётся в петле только для рискованного — через подтверждение (autonomy уровня 2). PM **не меняет свой workflow**: агент подключается к тем же инструментам (тот же Трекер, те же чаты).

```mermaid
flowchart LR
    subgraph conveyor["PM-конвейер — что автоматизируем"]
        direction LR
        IN["📥 ВХОД<br/>встречи, переписка,<br/>дедлайны"] 
        UND["🧠 ПОНЯТЬ<br/>что нужно сделать<br/>(LLM-рассуждение)"]
        ACT["⚙️ ДЕЙСТВИЕ<br/>задачи в Трекере,<br/>уведомления"]
        NOTIFY["📤 УВЕДОМИТЬ<br/>команду, PM"]
        IN --> UND --> ACT --> NOTIFY
    end

    HUMAN["👤 Человек<br/>подтверждает<br/>только рискованное"]
    ACT -.->|"risk ≥ medium"| HUMAN
    HUMAN -.->|"да / правка"| ACT

    style IN fill:#e3f2fd,color:#000
    style UND fill:#fff3e0,color:#000
    style ACT fill:#e8f5e9,color:#000
    style NOTIFY fill:#f3e5f5,color:#000
    style HUMAN fill:#ffebee,color:#000
```

**Метрика успеха (главная):** *Acceptance rate* — доля действий агента, которые человек принимает без правок. Растёт со временем → агент учится команде → больше автономии → больше сэкономленного времени PM.

---

## 2. Где мы сейчас vs целевое видение

Видение (`discovery/`) описывает зрелую платформу. Реальность — пройдены фундамент и часть ядра. Таблица показывает честную картину:

| Возможность (видение) | Статус | Комментарий |
|----------------------|:------:|-------------|
| Ядро платформы (config, db, llm, tools) | ✅ Готово | YandexGPT напрямую (не LiteLLM — проще, работает) |
| ReAct-цикл + Autonomy Gate (L2) | ✅ Готово | `core/react.py` — авто-low, confirm medium/high, resume |
| Tracker-интеграция (6 тулзов) | ✅ Готово | полный CRUD + поиск + переходы |
| Code-first агенты + автодискавери | ✅ Готово | агент = класс в `agents/`, без деплоя БД |
| JSON-RPC оркестратор (in-process) | ✅ Готово | multi-agent в одном процессе |
| Observability backbone (Action/Trace/Confirm) | ✅ Схема + запись | таблицы есть, ReActRunner пишет |
| Мониторинг (Prometheus/Grafana) | ✅ Готово | инфра-метрики + app-метрики |
| CI/CD на тест-VPS | ✅ Готово | push develop → авто-деплой |
| **Telegram-адаптер** | 🔴 Нет | следующий шаг, нужен для confirm/демо |
| **Control plane (агенты из БД)** | 🟡 Схема-only | `AgentSpec`/`AgentInstance` есть, оркестратор их не читает |
| **Scheduler / cron** | 🟡 Схема-only | `ScheduledJob` есть, нет демона-исполнителя |
| **call_agent (делегирование)** | 🔴 Нет | оркестратор не умеет агент→агент |
| **Meeting Summarizer** | 🔴 Нет | агент #1 из must-have |
| **Correspondence Analyzer** | 🔴 Нет | агент #2 |
| **Analytics Agent + метрики** | 🔴 Нет | флаг есть, реализации нет |
| **Алерты (дедлайны/SLA)** | 🔴 Нет | флаг `enable_alerts`, нет системы |
| **Дашборд (3 UI)** | 🔴 Нет | весь observability-слой есть в данных |
| **RBAC (User/Role)** | 🔴 Нет схемы | нужны новые таблицы |
| **Слой памяти (профили/проект/граф)** | 🔴 Нет схемы | future, нужны новые таблицы |
| **Networked A2A (agent cards/registry)** | 🔴 Нет | сознательно отложено (см. §5) |

**Вывод:** фундамент и механизм автономии готовы (это самое сложное). Не хватает **наполнения** (агенты, адаптеры, фоновые процессы) и **поверхности** (Telegram, дашборд).

---

## 3. Покрывает ли текущая база БД дальнейшие шаги?

Прямой ответ на ключевой вопрос. Разбор по таблицам — **что уже готово принять будущую функциональность, а где нужны новые схемы.**

```mermaid
flowchart TB
    subgraph ready["✅ ГОТОВЫ принять будущие фичи"]
        direction LR
        T1["organizations<br/>teams"]
        T2["agent_specs<br/>agent_instances<br/>(+overlay = layered config)"]
        T3["actions · traces<br/>confirms<br/>(observability backbone)"]
        T4["runtime_configs<br/>(per-team autonomy)"]
        T5["scheduled_jobs<br/>(self-scheduling)"]
        T6["action_feedback<br/>(петля обучения)"]
    end

    subgraph gaps["🔴 НУЖНЫ новые таблицы"]
        direction LR
        G1["users · roles<br/>role_bindings<br/>→ для RBAC"]
        G2["person_profiles<br/>project_memory<br/>team_edges<br/>→ для памяти PM"]
        G3["tracker_snapshots<br/>→ для метрик/лидерборда"]
        G4["agent_cards · a2a_tasks<br/>→ только если networked A2A"]
    end

    style ready fill:#e8f5e9,color:#000
    style gaps fill:#ffebee,color:#000
```

### Детально

| Будущий шаг | Покрытие БД | Что нужно |
|-------------|:-----------:|-----------|
| Telegram-адаптер | ✅ полное | ничего — сессии в `traces`, действия в `actions` |
| Control plane (агенты из БД) | ✅ полное | `agent_specs` + `agent_instances.overlay` готовы; нужен **код** (оркестратор должен читать БД, а не только классы) |
| Scheduler / алерты | ✅ полное | `scheduled_jobs` готова; нужен **демон** (tick + SKIP LOCKED) |
| call_agent / multi-agent | ✅ полное | `traces` хранит шаги; делегирование — **код**, не схема |
| Meeting/Correspondence/Analytics агенты | ✅ полное | агенты = код, конфиг в `agent_specs`; схема готова |
| Фидбек-петля | ✅ полное | `action_feedback` готова, нужен дашборд для сбора |
| Метрики/лидерборд | 🟡 частично | нужна таблица `tracker_snapshots` (read-модель Трекера) |
| RBAC / multi-PM | 🔴 нет | нужны `users`, `roles`, `role_bindings` |
| Память PM (профили/проект/граф) | 🔴 нет | нужны `person_profiles`, `project_memory`, `team_edges` |
| Networked A2A | 🔴 нет | нужны `agent_cards`, `a2a_tasks` — **но это отложено** |

**Итог:** **~70% будущих шагов покрыты текущей схемой.** Фаза 0-5 (до дашборда) требует только **2 новых таблицы** (`tracker_snapshots` для метрик, опционально). RBAC и память — отдельные блоки схемы, нужны только при масштабировании за пределы одной команды и в «будущем» соответственно. Критично: **layered config заложен правильно** (`agent_instances.overlay`) — это самое больное для ретрофита, и оно уже есть.

Одна техническая чистка: таблица `langchain_checkpoints` в миграции **не используется** (мы не на LangGraph) — её можно удалить при следующей миграции.

---

## 4. Стратегические принципы: что оставляем, что добавляем, что откладываем

Видение из discovery описывает «тяжёлый» стек (networked A2A через a2a-sdk, LangGraph, LiteLLM, control-plane агенты в БД). Реальная реализация пошла «легче» и в ряде мест — **лучше для текущего масштаба**. Зафиксируем осознанные решения:

| Аспект | Видение discovery | Реализовано | Решение |
|--------|-------------------|-------------|---------|
| LLM-слой | LiteLLM | Прямой YandexGPT + fallback в `LLMSettings` | ✅ **Оставляем** — проще, меньше зависимостей, fallback уже есть |
| Движок агента | LangGraph | Кастомный `ReActRunner` | ✅ **Оставляем** — полный контроль, нет тяжёлой зависимости |
| Мультиагентность | Networked A2A (сервис на агента) | In-process оркестратор | ✅ **Оставляем in-process**, добавим `call_agent` (см. §5) |
| Определение агента | AgentSpec в БД (control plane) | Python-класс (code-first) | 🔀 **Гибрид** (см. ниже) |

### Развилка: code-first vs control-plane агенты

Сейчас агент — это Python-класс (`agents/pm_agent.py`), который автодискаверится. Видение говорит про `AgentSpec` в БД (менять промпт без деплоя). **Рекомендация — гибрид:**

- **Структура агента** (какие тулзы, какой граф) — остаётся в коде (code-first, безопасно, версионируется в git).
- **Параметры** (промпт, модель, пороги автономии, on/off) — читаются из `agent_specs` + `agent_instances.overlay`, с фолбэком на значения класса.

Это даёт лучшее из двух миров: разработчик задаёт каркас и tools (граница безопасности), PM/Dev правит промпт и пороги через дашборд без деплоя. Схема под это **уже готова** — нужно только научить `OrchestratorService` читать БД-оверлей поверх класса.

```mermaid
flowchart LR
    CLASS["Python-класс агента<br/>(code, git)<br/>name, tools, граф"]
    SPEC["agent_specs<br/>(БД, Dev через дашборд)<br/>промпт, модель"]
    OVERLAY["agent_instances.overlay<br/>(БД, PM через дашборд)<br/>team-инструкции, пороги confirm"]
    EFF["Effective Agent<br/>(то, что реально работает)"]

    CLASS --> EFF
    SPEC -->|"переопределяет"| EFF
    OVERLAY -->|"переопределяет"| EFF

    style CLASS fill:#e3f2fd,color:#000
    style SPEC fill:#fff3e0,color:#000
    style OVERLAY fill:#f3e5f5,color:#000
    style EFF fill:#e8f5e9,color:#000
```

---

## 5. call_agent: мультиагентность без networked A2A

Видение требует делегирования агент→агент (Orchestrator зовёт Meeting Summarizer). Discovery предлагает networked A2A (каждый агент — отдельный сервис, agent cards, registry). **Это избыточно** для одной команды и нескольких агентов в одном процессе.

**Решение — следовать собственному принципу discovery: «начать in-process, вынести в сервис потом, не меняя код вызывающего».**

`call_agent` — это обычный `@platform_tool`, который под капотом зовёт другой агент через `OrchestratorService`. Сейчас — в том же процессе. Позже, если понадобится независимое масштабирование — тот же tool начинает делать HTTP-вызов к отдельному сервису. Код агента-вызывателя не меняется.

```mermaid
flowchart TB
    ORCH["PM Orchestrator<br/>(ReAct)"]
    TOOL["call_agent:meeting_summarizer<br/>(@platform_tool)"]
    SVC["OrchestratorService<br/>.invoke('meeting_summarizer', ...)"]
    MS["Meeting Summarizer<br/>(ReAct)"]

    ORCH -->|"LLM решает делегировать"| TOOL
    TOOL --> SVC
    SVC --> MS
    MS -->|"action items"| SVC
    SVC -->|"результат в историю"| ORCH

    NOTE["📍 Сейчас: in-process вызов<br/>📍 Потом: HTTP к сервису —<br/>код агента не меняется"]
    TOOL -.-> NOTE

    style NOTE fill:#fff9c4,color:#000
```

Networked A2A (agent cards, registry, `/a2a` endpoint, call_chain, loop-prevention) добавляется **только** когда появится реальная потребность: разные команды/орги с изоляцией, внешние агенты, независимый деплой/скейл специалистов. До тех пор — лишняя сложность.

---

## 6. Целевая архитектура (компоненты)

```mermaid
flowchart TB
    subgraph entry["Точки входа"]
        direction LR
        TG["Telegram<br/>(aiogram)"]
        WEB["Web / curl"]
        CRON["Scheduler<br/>(cron tick)"]
    end

    subgraph api["platform-api :8000 — тонкий HTTP/транспорт"]
        ROUTES["/chat · /confirm · /agents/*<br/>/actions · /metrics"]
        TGADAPTER["Telegram webhook<br/>+ inline-кнопки confirm"]
    end

    subgraph orch["pm-orchestrator :8001 — мозг"]
        direction TB
        RPC["JSON-RPC 2.0 /rpc"]
        SVC["OrchestratorService<br/>discover · invoke · resume"]
        subgraph agents["Агенты (автодискавери)"]
            PM["PM Orchestrator"]
            MS["Meeting Summarizer"]
            CA["Correspondence Analyzer"]
            AN["Analytics Agent"]
        end
        RUNNER["ReActRunner<br/>+ Autonomy Gate"]
        SCHED["Scheduler daemon<br/>tick + SKIP LOCKED"]
    end

    subgraph corelib["packages/core — общая библиотека"]
        direction LR
        TOOLS["ToolRegistry<br/>tracker_* · alert · call_agent · schedule_task"]
        LLMC["LLM client<br/>YandexGPT + fallback"]
        EFFCFG["Effective Config<br/>spec + overlay"]
    end

    PG[("PostgreSQL<br/>actions · traces · confirms<br/>agent_specs · instances<br/>scheduled_jobs · feedback<br/>runtime_configs · tracker_snapshots")]
    TRACKER["Яндекс Трекер API v3"]
    GRAF["Prometheus + Grafana"]

    TG --> TGADAPTER --> ROUTES
    WEB --> ROUTES
    CRON --> SCHED
    ROUTES -->|"JSON-RPC"| RPC
    RPC --> SVC --> agents --> RUNNER
    RUNNER --> TOOLS
    RUNNER --> LLMC
    RUNNER --> EFFCFG
    SCHED --> SVC
    TOOLS --> TRACKER
    RUNNER --> PG
    EFFCFG --> PG
    SCHED --> PG
    orch --> GRAF
    api --> GRAF

    style entry fill:#e3f2fd,color:#000
    style orch fill:#fff3e0,color:#000
    style corelib fill:#e8f5e9,color:#000
```

Жирным выделены **новые** компоненты относительно текущего состояния: Telegram-адаптер, агенты MS/CA/AN, Scheduler daemon, новые тулзы (`alert`, `call_agent`, `schedule_task`), `tracker_snapshots`, Effective Config (spec+overlay).

---

## 7. Бизнес-логика: 5 must-have потоков

Исходная доска требований раскладывается на потоки. Каждый — ценность + техническая реализация.

### Поток 1 — Встречи → задачи (ядро ценности)

```mermaid
sequenceDiagram
    participant U as PM / Telegram
    participant O as PM Orchestrator
    participant MS as Meeting Summarizer
    participant T as Трекер
    participant H as Человек (confirm)

    U->>O: транскрипт встречи
    O->>MS: call_agent(транскрипт)
    MS-->>O: action items [задача1, задача2, задача3]
    O->>O: для каждой → tracker_create_issue (risk=medium)
    O->>H: «Создать 3 задачи со встречи?» (Telegram-кнопки)
    H-->>O: ✅ да
    O->>T: создать задачи
    T-->>O: DARKHORSE-101..103
    O-->>U: «Создал 3 задачи: ...»
```

**Ценность:** PM не тратит 20-30 мин после каждой встречи на занесение задач. Самый частый и нелюбимый кусок рутины.

### Поток 2 — Переписка → изменения/вводные

```mermaid
flowchart LR
    MSG["Новые сообщения<br/>в чате"] --> CA["Correspondence<br/>Analyzer"]
    CA --> DEC{Что нашёл?}
    DEC -->|"изменение"| UPD["tracker_update_issue<br/>(confirm)"]
    DEC -->|"не хватает данных"| REQ["request_input<br/>у человека"]
    DEC -->|"риск срыва"| ESC["alert PM<br/>(эскалация)"]
    style CA fill:#fff3e0,color:#000
```

**Ценность:** ничего не теряется в чатах. Решения из переписки автоматически отражаются на доске.

### Поток 3 — Алерты (проактивность)

```mermaid
flowchart LR
    TICK["Scheduler tick<br/>(каждую минуту)"] --> JOB["scheduled_jobs<br/>SKIP LOCKED"]
    JOB --> CHECK["Проверка:<br/>дедлайны, застрявшие,<br/>SLA-нарушения"]
    CHECK --> ALERT["alert tool →<br/>Telegram PM/исполнителю"]
    style TICK fill:#e3f2fd,color:#000
```

**Ценность:** проблемы всплывают до того, как стали пожаром. PM не держит всё в голове.

### Поток 4 — Канбан (детерминированные тулзы)

Ведение доски — это не отдельный агент, а **набор тулзов** (`tracker_*`), которые Orchestrator вызывает по результатам потоков 1-2. Уже реализовано.

### Поток 5 — Urgent confirm (человек в петле)

Реализовано через Autonomy Gate (см. §8). Самый важный механизм доверия.

---

## 8. Autonomy Gate — механизм доверия (реализован)

```mermaid
flowchart TD
    CALL["Агент хочет вызвать tool"] --> GATE{Autonomy Gate}
    GATE -->|"risk=low<br/>и не в always_confirm"| AUTO["✅ выполнить сразу<br/>+ залогировать"]
    GATE -->|"risk ≥ medium<br/>или always_confirm"| INT["⏸ interrupt →<br/>confirm в Telegram"]
    INT --> ANS{Ответ человека}
    ANS -->|"да"| EXEC["выполнить"]
    ANS -->|"нет / правка"| FB["вернуть фидбек агенту<br/>→ переосмыслить"]

    CFG["runtime_configs<br/>(per-team, без деплоя)<br/>что считать рискованным"]
    CFG -.->|"настраивает пороги"| GATE

    style AUTO fill:#e8f5e9,color:#000
    style INT fill:#fff3e0,color:#000
    style FB fill:#ffebee,color:#000
```

**Ценность:** доверие выстраивается постепенно. Тестовая команда — confirm почти на всё; по мере роста acceptance rate пороги поднимаются → больше автономии. Kill-switch (`agent_instances.enabled=false`) выключает агента мгновенно.

---

## 9. Глобальный roadmap (приоритизированный)

Текущее состояние: **Фаза 0 завершена + механизм Фазы 3 (Autonomy Gate) готов досрочно.** Реалистичный порядок дальше — по убыванию отношения ценность/усилие:

```mermaid
flowchart LR
    subgraph now["✅ Сделано"]
        N["Ядро · ReAct · Autonomy Gate<br/>Tracker · Оркестратор · CI/CD"]
    end

    subgraph s1["Шаг 1 — Замкнуть демо"]
        A["Telegram-адаптер<br/>+ DB-персист в проде<br/>+ call_agent tool"]
    end

    subgraph s2["Шаг 2 — Ценность встреч"]
        B["Meeting Summarizer<br/>встреча → задачи<br/>(главный поток)"]
    end

    subgraph s3["Шаг 3 — Проактивность"]
        C["Scheduler daemon<br/>+ alert tool<br/>дедлайны/SLA"]
    end

    subgraph s4["Шаг 4 — Прозрачность"]
        D["Дашборд (dev+admin минимум)<br/>+ Effective Config (spec/overlay)<br/>+ фидбек-петля"]
    end

    subgraph s5["Шаг 5 — Расширение"]
        E["Correspondence + Analytics<br/>+ tracker_snapshots<br/>+ метрики/лидерборд"]
    end

    subgraph fut["Будущее"]
        F["RBAC (multi-team)<br/>Память (профили/проект/граф)<br/>Networked A2A"]
    end

    now --> s1 --> s2 --> s3 --> s4 --> s5 --> fut

    style now fill:#e8f5e9,color:#000
    style s1 fill:#fff3e0,color:#000
    style fut fill:#eceff1,color:#000
```

### Матрица ценность / усилие

| Шаг | Ценность | Усилие | DB-изменения |
|-----|:--------:|:------:|--------------|
| 1. Telegram + персист + call_agent | 🔥 высокая (демо, мультиагент) | средне | нет |
| 2. Meeting Summarizer | 🔥 высокая (ядро продукта) | средне | нет |
| 3. Scheduler + алерты | высокая (проактивность) | средне | нет (`scheduled_jobs` есть) |
| 4. Дашборд + Effective Config | высокая (доверие, тюнинг) | высокое | нет |
| 5. Correspondence/Analytics/метрики | средняя | высокое | +`tracker_snapshots` |
| Будущее: RBAC | низкая пока 1 команда | высокое | +`users/roles/bindings` |
| Будущее: память | высокая, но рано | очень высокое | +`profiles/project/edges` |
| Будущее: networked A2A | низкая пока 1 процесс | высокое | +`agent_cards/a2a_tasks` |

---

## 10. Технические аспекты по шагам

### Шаг 1 — Замкнуть демо
- **Telegram-адаптер** (`platform-api`, aiogram): webhook → `/chat`, inline-кнопки ✅/❌ → `/confirm/{id}`. Сессия = chat_id.
- **DB-персист в оркестраторе**: сейчас `OrchestratorService` хранит сессии in-memory; передать `db_session` в `ReActRunner` (он уже умеет писать в `traces/actions/confirms`).
- **`call_agent` tool**: `@platform_tool`, вызывает `OrchestratorService.invoke(target_agent, ...)`. Регистрируется автоматически для каждого агента.

### Шаг 2 — Meeting Summarizer
- Новый файл `agents/meeting_summarizer.py` (BaseAgent, без тулзов Трекера — только рассуждение).
- Вход: транскрипт (пока — ручная вставка в чат; источник транскриптов — открытая развилка).
- PM Orchestrator получает `call_agent:meeting_summarizer` автоматически.

### Шаг 3 — Scheduler
- Демон в `pm-orchestrator`: `asyncio` loop `* * * * *`, `SELECT ... FOR UPDATE SKIP LOCKED` по `scheduled_jobs.next_run <= now()`.
- `schedule_task` tool — агент сам ставит задачи. Guardrails: квота, TTL, max_runs, confirm на recurring.
- `alert` tool — уведомление в Telegram.

### Шаг 4 — Дашборд + Effective Config
- Минимум: dev-UI (реестр агентов, редактор промпта, просмотр трейсов) + admin-минимум (лента действий, kill-switch, пороги).
- **Effective Config**: `OrchestratorService` при старте/запросе мёржит `agent_specs` + `agent_instances.overlay` поверх значений класса.
- Фидбек: сбор `action_feedback` через UI → сигнал для тюнинга промптов.

### Шаг 5 — Расширение
- `Correspondence Analyzer`, `Analytics Agent` — новые классы агентов.
- `tracker_snapshots` — read-модель: cron тянет story points / счётчики из Трекера → метрики, лидерборд, burndown.

### Будущее
- **RBAC**: `users`, `roles`, `role_bindings` (user_id, role, scope_type, scope_id). 4 роли из `06_org_roles.md`.
- **Память**: `person_profiles`, `project_memory`, `team_edges` + tool `get_context()` + RAG. Архитектура не меняется — это ещё один tool.
- **Networked A2A**: `agent_cards`, `a2a_tasks`, `/a2a` endpoint, call_chain/max_depth. Только при реальной потребности изоляции/скейла.

---

## 11. Открытые развилки (требуют решения)

1. **Источник транскриптов встреч** — Telemost API / запись + Whisper / ручная вставка. Определяет триггер Meeting Summarizer. *Рекомендация для старта: ручная вставка.*
2. **Code-first vs control-plane** — рекомендован гибрид (§4). Подтвердить.
3. **Когда вводить RBAC** — рекомендация: только при выходе за пределы одной тестовой команды.
4. **Чистка `langchain_checkpoints`** — таблица не используется, удалить в следующей миграции.

---

## 12. Сводка одним абзацем

Фундамент платформы и самый сложный механизм (автономия уровня 2 с human-in-the-loop) **готовы и работают**. Текущая БД-схема покрывает **~70% будущих шагов** без изменений — критичный layered config уже заложен правильно. Ближайшая ценность — **замкнуть демо через Telegram и добавить Meeting Summarizer** (ядро продукта), затем проактивные алерты и дашборд. Тяжёлые элементы видения (networked A2A, LangGraph, LiteLLM) сознательно заменены на более лёгкие эквиваленты — и это правильно для текущего масштаба; они добавляются только при реальной потребности. Новые таблицы нужны лишь для RBAC и памяти — а это «будущее», не ближайшие шаги.
