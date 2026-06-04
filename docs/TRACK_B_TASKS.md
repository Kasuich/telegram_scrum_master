# Трек B — Ядро рантайма: разбивка на задачи

> **Слой:** «мозг» / runtime — `pm-orchestrator` + `packages/core`.
> **Цель:** действия пишутся в БД, cron-задачи исполняются, агент может звать агента.
> **Критический путь:** B стартует первым — персист разблокирует данные для Трека D, `call_agent` — для Трека C.

---

## Контекст: что уже есть в коде (не переписывать!)

| Компонент | Состояние | Файл |
|-----------|-----------|------|
| `ReActRunner` с **полным DB-персистом** | ✅ готов + per-call `_RunCtx` (B1) | `core/react.py` |
| `_db_persist_action` / `_db_load_session` / `_db_resolve_confirm` | ✅ реализованы, протянут `ctx` | `core/react.py` |
| `OrchestratorService.invoke/resume` | ✅ DB-режим подключён (B1) | `orchestrator.py` |
| `ScheduledJob` модель (cron_expr, next_run, max_runs, run_count) | ✅ схема готова | `models.py:328` |
| `RuntimeConfig` (auto_risk/confirm_risk/always_confirm) | ✅ готов | `config.py:201` |
| `Action/Trace/Confirm/AgentSpec/AgentInstance` | ✅ схемы готовы | `models.py` |
| JSON-RPC `invoke/resume/get_actions/list_agents` | ✅ работает | `rpc.py` |

**Главный гэп (B1 — закрыт ✅):** `OrchestratorService` теперь в `invoke`/`resume` открывает сессию per-request и пробрасывает `db_session`+`team_id` в раннер. Персист в `react.py` ожил. Схема и команда создаются на старте (`ensure_schema_and_seed`).

---

## B1 — DB-персист в оркестраторе ✅ ВЫПОЛНЕНО

**Проблема архитектуры:** `ReActRunner.__init__` принимает `db_session`, но раннеры создаются один раз при дискавери. `AsyncSession` живёт per-request, нельзя привязать на старте.

### B1.0 — Решение: сессия per-request
- [x] Выбран подход: `db_session`/`team_id` передаются в `invoke()`/`resume()` (keyword-only), не в `__init__`. Проброс через per-call dataclass `_RunCtx` — concurrency-safe для шаренного раннера.

### B1.1 — Рефактор `ReActRunner` под session-per-call
- [x] `invoke(message, session_id, *, db_session=None, team_id=None)`
- [x] `resume(confirm_id, approved, *, db_session=None, team_id=None)`
- [x] `_RunCtx` протянут через `_run_loop` и все `_load/_save/_persist`-методы (вместо `self.db_session`)
- [x] Обратная совместимость: без `db_session` → in-memory (14 тестов `test_react.py` зелёные)
- [x] `_session_uuid()` — детерминированный uuid5 для не-UUID session_id (Telegram chat_id и т.п.)

### B1.2 — Подключить сессию в `OrchestratorService`
- [x] `invoke`/`resume` открывают `async with get_session() as s:` и пробрасывают в раннер (DB-режим)
- [x] `configure_persistence()` — включает DB-режим при наличии `database_url` + `default_team_id`
- [x] Коммит — через `get_session()` (commit on exit), rollback при исключении
- [x] Фолбэк в in-memory, если DB-инициализация упала (сервис всё равно стартует)

### B1.3 — Сидовые данные для FK + схема
- [x] `core/seed.py::ensure_default_team()` — идемпотентно создаёт `Organization` + `Team` с фиксированным UUID
- [x] `core/db.py::create_all_tables()` — идемпотентное создание схемы на старте (миграции на VPS не автогонятся)
- [x] `ensure_schema_and_seed()` в lifespan оркестратора
- [x] `DEFAULT_TEAM_ID` в `config.py` (AppConfig), `.env.example`, `docker-compose.yml`, `deploy-test.yml`

### B1.4 — Тесты
- [x] `test_react_persist.py`: `_session_uuid` (passthrough/детерминизм/различие)
- [x] `ensure_default_team` идемпотентность (создание + повторный вызов)
- [x] Round-trip против реального Postgres (invoke→confirm-строка→resume→`completed`/`approved`) — гейтнут на `TEST_DATABASE_URL`
- [x] Регресс: in-memory путь работает; полный сьют 297 passed

**DoD B1:** ✅ механизм готов. Round-trip проверяется гейтнутым тестом; на тест-VPS — после merge в `develop` (есть Postgres). Локально нет Postgres/docker, поэтому round-trip там скипается.

---

## B2 — Scheduler daemon (приоритет №2)

Фоновый исполнитель `scheduled_jobs`. Демон внутри `pm-orchestrator`.

### B2.1 — Парсер cron
- [ ] Выбрать либу (`croniter`) — добавить в `pyproject.toml` оркестратора
- [ ] Хелпер `compute_next_run(cron_expr, after) -> datetime`

### B2.2 — Tick-loop с `SKIP LOCKED`
- [ ] `core/scheduler.py` (или `pm_orchestrator/scheduler.py`): `asyncio` loop, тик раз в минуту
- [ ] `SELECT ... FROM scheduled_jobs WHERE enabled AND next_run <= now() FOR UPDATE SKIP LOCKED LIMIT N`
- [ ] Для каждой джобы: `OrchestratorService.invoke(agent, payload.message, session_id)` 
- [ ] После запуска: `run_count += 1`, пересчёт `next_run`, при `run_count >= max_runs` → `enabled=false`
- [ ] Ошибка джобы не валит весь тик (try/except на джобу, лог)

### B2.3 — Запуск демона
- [ ] Стартовать loop в `lifespan` (`rpc.py`) как `asyncio.create_task`
- [ ] Graceful shutdown: отмена таски на остановке
- [ ] Флаг `SCHEDULER_ENABLED` в конфиге (выключать в тестах/деве)

### B2.4 — Тесты
- [ ] `compute_next_run` для типовых выражений (`* * * * *`, `0 9 * * *`)
- [ ] Джоба с `next_run` в прошлом → исполняется, `run_count` растёт
- [ ] `max_runs` достигнут → `enabled=false`
- [ ] Две конкурентные «реплики» тика не берут одну джобу дважды (SKIP LOCKED)

**DoD B2:** строка в `scheduled_jobs` с прошедшим `next_run` приводит к запуску агента; повторы по расписанию работают.

---

## B3 — `schedule_task` tool (агент сам ставит себе задачи)

### B3.1 — Тул
- [ ] `@platform_tool(name="schedule_task", risk="medium")` в `core/` (напр. `core/scheduler_tools.py`)
- [ ] Параметры: `cron_expr`, `message`, `name`, `max_runs?`
- [ ] Создаёт `ScheduledJob` (привязка к текущему `agent_instance_id`)

### B3.2 — Guardrails
- [ ] Квота: лимит активных джоб на агента/команду
- [ ] TTL по умолчанию (`max_runs` или дата окончания), чтобы не плодить вечные джобы
- [ ] `risk=medium` → recurring-джобы идут через confirm (Autonomy Gate уже это делает)
- [ ] Валидация `cron_expr` (через `croniter`, ошибка → понятное сообщение агенту)

### B3.3 — Контекст исполнения
- [ ] Тулу нужен `agent_instance_id`/`team_id` — продумать передачу контекста в тул (см. развилку «контекст тула»)

### B3.4 — Тесты
- [ ] Валидный вызов → строка в `scheduled_jobs`
- [ ] Невалидный cron → `ToolValidationError`
- [ ] Превышение квоты → отказ с сообщением

**DoD B3:** агент в диалоге может попросить «напоминай каждый понедельник» → джоба создана (после confirm).

---

## B4 — `call_agent` tool (делегирование агент→агент)

In-process делегирование через `OrchestratorService` (см. `TARGET_ARCHITECTURE.md` §5).

### B4.1 — Тул
- [ ] `@platform_tool(name="call_agent", risk="low")` — параметры `target_agent`, `message`
- [ ] Под капотом зовёт `OrchestratorService.invoke(target_agent, message, sub_session_id)`
- [ ] Возвращает `reply` суб-агента в историю вызывающего

### B4.2 — Защита от рекурсии
- [ ] `call_depth` счётчик + `MAX_CALL_DEPTH` (напр. 3)
- [ ] Защита от self-call и циклов (A→B→A) — трекать цепочку вызовов
- [ ] Суб-сессия: производный `session_id` (напр. `{parent}:{target}`) для изоляции истории

### B4.3 — Доступ к сервису из тула
- [ ] Тул должен видеть `OrchestratorService` — решить через реестр/контекст (см. развилку)
- [ ] Суб-агент с `pending_confirm`: как пробросить confirm наверх? (для MVP — суб-агенты только low-risk, без confirm; задокументировать ограничение)

### B4.4 — Тесты
- [ ] Агент A зовёт агента B → ответ B в истории A
- [ ] Рекурсия глубже `MAX_CALL_DEPTH` → отказ
- [ ] Self-call → отказ

**DoD B4:** PM Orchestrator может вызвать `call_agent("meeting_summarizer", transcript)` и получить action items.

---

## B5 — Effective Config (мёрж класс < spec < overlay)

Совместно с Треком C; B владеет мёржем в оркестраторе.

### B5.1 — Загрузка spec/overlay из БД
- [ ] При дискавери/инвоке: подтянуть `AgentSpec` (по `name`) + `AgentInstance.overlay` (по `team_id`)
- [ ] Хелпер `build_effective_config(agent_class, spec, overlay) -> EffectiveConfig`

### B5.2 — Приоритеты
- [ ] `prompt`, `model`, пороги автономии: класс (дефолт) < `agent_specs` < `agent_instances.overlay`
- [ ] `RuntimeConfig` для раннера собирать из overlay (а не хардкод `auto/confirm` в `_register`)
- [ ] Фолбэк: нет записи в БД → значения класса (текущее поведение)

### B5.3 — Тесты
- [ ] overlay переопределяет промпт без деплоя
- [ ] нет spec/overlay → значения класса
- [ ] пороги confirm берутся из overlay

**DoD B5:** правка промпта/порогов в `agent_specs`/`overlay` меняет поведение агента без рестарта кода.

---

## Контракты, которые B отдаёт в день 1

| # | Контракт | Потребитель |
|---|----------|-------------|
| `call_agent:X` (имя тула, сигнатура `target_agent`, `message`) | Трек C |
| Effective Config: приоритет `класс < spec < overlay` | Трек C |
| Read-модели: поля `actions/traces/confirms` (уже в `models.py`) | Трек D |
| `DEFAULT_TEAM_ID` / сид команды | Трек D (для запросов) |

---

## Развилки (решить в начале)

1. **Сессия в раннере:** рефактор `invoke/resume(..., db_session=)` (рекомендую) **vs** создавать `ReActRunner` per-request. Первое — меньше аллокаций, но трогает сигнатуры; второе — проще, но раннер перестаёт быть долгоживущим.
2. **Контекст тула** (`team_id`, `agent_instance_id`, доступ к `OrchestratorService`): `ContextVar` (как `trace_id` в `logging.py`) **vs** явная передача через спец-параметр тула. Нужно для B3 и B4.
3. **`team_id` сейчас:** env `DEFAULT_TEAM_ID` + сид **vs** дождаться RBAC/`users` (Трек D). Рекомендую env+сид — не блокироваться.
4. **Confirm у суб-агентов** (B4): для MVP запретить confirm в делегированных вызовах (только low-risk) и задокументировать — иначе нужен проброс pending_confirm через цепочку.

---

## Порядок и зависимости

```
B1 (персист) ──┬──► разблокирует Трек D (реальные данные)
               │
B5 (eff.config)┘  (можно параллельно, опирается на B1-сессию)

B2 (scheduler) ──► B3 (schedule_task)   (B3 нужен tick из B2)

B4 (call_agent) ──► разблокирует Трек C (Meeting Summarizer делегирование)
```

**Рекомендуемая последовательность:** B1 → (B4 ∥ B2) → B3 → B5.
B1 первым (критический путь к D), B4 рано (критический путь к C), B2/B3 — проактивность, B5 — тюнинг.
