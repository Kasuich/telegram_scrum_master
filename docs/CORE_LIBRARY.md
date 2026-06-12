# Core-библиотека (`packages/core`)

> Общая библиотека, на которую опираются все сервисы. Здесь живут агент-фреймворк,
> ReAct-рантайм, LLM-клиент, интеграция с Трекером, расписания, питомец, битвы,
> аудит, дайджесты, подсистема оценки и ORM. Импортируется как `from core import …`.

Архитектурный контекст — [ARCHITECTURE](ARCHITECTURE.md); модель данных —
[DATA_MODEL](DATA_MODEL.md). Все модули — под `packages/core/src/core/`.

---

## Агент-фреймворк (code-first)

| Модуль | Что внутри |
|--------|------------|
| `agent.py` | `BaseAgent` (name/description/prompt/tools/llm_configs + `run()`), `LLMSettings` (модель, провайдер, температура, fallback-цепочка), `AgentResponse` |
| `bot.py` | `BaseBot` — верхнеуровневая единица (bot_id, платформы), авто-регистрация в реестре |
| `entry_point.py` | `EntryPoint` — один агент или меню агентов (`/команда`) |
| `registry.py` | `BotRegistry` + `get_bot_registry()` — глобальный реестр ботов |

Подробный гайд по созданию агентов — [agents.md](agents.md) и [ADDING_AGENTS.md](ADDING_AGENTS.md).

---

## ReAct-рантайм и стадии

| Модуль | Что внутри |
|--------|------------|
| `react.py` | `ReActRunner`, `AgentResult`, `PendingConfirm` — главный цикл «LLM → tool → confirm/execute → resume», персист хода в `traces`, восстановление из `pending_confirm` |
| `stage_graph.py` | Граф стадий (`StageId`: INTAKE/STATUS/BOARD/TRANSITION/QUERY/REORG/PROACTIVE/HYGIENE/MEETING_SYNC/DIALOG), whitelist инструментов и guard'ы на стадию |
| `stage_router.py` | Определение стадии хода по сообщению (правила + LLM-классификатор) |
| `turn_guards.py` | Предикаты, блокирующие невалидные ходы (комментарий без задачи и т.п.) |
| `turn_plan.py` | Разбиение сложного запроса на управляемые шаги внутри хода |
| `invocation.py` | `InvocationContext` — request-scoped метаданные (актор, доска, транспорт) |
| `prompts.py` | Системные промпты (`PM_AGENT_SYSTEM_PROMPT`) и форматтеры confirm/ошибок |

Детерминированный граф стадий описан в [pm_agent_stage_graph.md](pm_agent_stage_graph.md).

---

## LLM и конфигурация

| Модуль | Что внутри |
|--------|------------|
| `llm.py` | `LLMClient` — поддержка **OpenRouter** (chat completions) и **Yandex Cloud** (OpenAI-совместимый Responses API); tool-calling, стриминг, ретраи с бэкоффом (408/409/425/429/5xx), учёт токенов |
| `config.py` | Pydantic-настройки: `Config` + секции БД/Yandex/Tracker/MCP/LLM/дайджест/стендап; `Config.for_team(...)` для team-overlay; `get_config()` |
| `effective_config.py` | `build_effective_config(...)` — сведение слоёв `class < AgentSpec < AgentInstance.overlay` в `EffectiveAgentConfig` |

> Модели агентов задаются прямо в их классах (`LLMSettings(model=..., provider="openrouter")`).
> Сейчас: `pm_agent` и `meeting_summarizer` → `google/gemini-3.1-flash-lite`,
> `audit_agent` → `google/gemini-3.1-pro`. Реестр env-переменных — [CONFIGURATION](CONFIGURATION.md).

---

## Инструменты и интеграция с Трекером

| Модуль | Что внутри |
|--------|------------|
| `tools.py` | `@platform_tool` (name, risk, scopes), `ToolRegistry`, `get_registry()` — основа системы инструментов |
| `tracker.py` | `TrackerClient` — async-клиент Яндекс Трекер REST API v3 (httpx), ретраи на 429/5xx, поддержка org-типов `cloud`/`360` |
| `tracker_mcp.py` | `TrackerMCPClient` — клиент Tracker MCP gateway (HTTP+SSE), источник CamelCase-инструментов для агента |
| `tracker_tools.py` | Нативные `tracker_*` инструменты (`tracker_create_issue`, `tracker_board_snapshot`, спринты/эпики и др.) |
| `tracker_tool_helpers.py` | Хелперы: нормализация дат/дедлайнов, парсинг, извлечение ключей задач, дефолтная доска |
| `assignee_resolver.py` | Fuzzy-матчинг логина исполнителя по составу команды |
| `issue_dedup.py` | Поиск дублей перед созданием задачи |
| `comment_format.py` | Форматирование комментариев для Трекера |
| `audit_tools.py` | Инструмент `audit_board_digest` — обёртка над `audit.py` для агента |
| `backlog_tools.py` | Инструменты применения backlog-плана (`tracker_apply_backlog_plan` и др.) |

Доступ агента к Трекеру идёт через **Tracker MCP** (`tracker_mcp.py`), а сервисы
(дайджесты, аудит, личная доска консоли) — напрямую через **REST** (`tracker.py`).

---

## Планирование задач (backlog & goals)

| Модуль | Что внутри |
|--------|------------|
| `goal.py` | `GoalItem`/`GoalPlan`, `build_goal_plan(...)` — декомпозиция запроса в упорядоченные цели с success-критериями |
| `backlog_plan.py` | `BacklogItem`/`BacklogPlan` — список задач к созданию из целей (intent → поля Трекера) |
| `backlog_scheduler.py` | Планировщик исполнения backlog-плана |
| `backlog_context.py` | Контекст исполнения backlog |

---

## Расписания и проактивные сценарии

| Модуль | Что внутри |
|--------|------------|
| `scheduler.py` | `SchedulerDaemon` + `compute_next_run(cron)` — обработка `ScheduledJob` через `SELECT … FOR UPDATE SKIP LOCKED` (мульти-реплика) |
| `cron_schedule.py` | Утилиты cron поверх croniter (парсинг/валидация) |
| `daily_digest.py` | Часовой командный дайджест из Трекера → Telegram |
| `deadline_reminders.py` | Напоминания об овердью/скоро-дедлайнах, маршрутизация по ролям |
| `standup_poll.py` | Стендап-опросы участников в Telegram + сбор ответов |
| `telemost_shortcut.py` | Генерация/сокращение ссылок Telemost для встреч |

---

## Метрики доски и аудит

| Модуль | Что внутри |
|--------|------------|
| `board_metrics.py` | Чистые функции над сырыми задачами Трекера: lead/cycle time, overdue, in-progress, resolved |
| `audit.py` | `gather_board_issues()` + `build_audit_digest()` — снимок здоровья очереди, гигиена (нет дедлайна/оценки/исполнителя), нагрузка по людям |

---

## Питомец «Скрамик» и битвы

| Модуль | Что внутри |
|--------|------------|
| `pet.py` | Детерминированная математика уровня/настроения/характеристик; 10 видов, env-тюнинг (`PET_*`) |
| `pet_battle.py` | Движок битв: `run_royale()` (командный турнир) и `run_duel()` (1-на-1), seedable RNG |
| `scrumik_sprites.py` | ASCII-спрайты/кадры анимации 10 видов |
| `scrumik_render.py` | Рендер спрайтов и текста |
| `battle_image.py` | Генерация картинки результата битвы (Pillow; шрифт `assets/DejaVuSans.ttf`) |

Продуктовый дизайн и ассеты — [SCRUMIC_DESIGN](SCRUMIC_DESIGN.md).

---

## Хранилище, БД и сервисное

| Модуль | Что внутри |
|--------|------------|
| `db.py` | Async-движок SQLAlchemy, `get_session()`, `create_all_tables()`, `health_check()` |
| `models.py` | Все ORM-модели (см. [DATA_MODEL](DATA_MODEL.md)) |
| `repositories/telegram_message.py` | Репозиторий истории Telegram-сообщений (пагинация, курсор) |
| `seed.py` | `ensure_default_team()` и бутстрап агент-инстансов |
| `metrics.py` | Prometheus-счётчики/гистограммы (LLM, инструменты, трейсы, confirm) |
| `logging.py` | `configure_logging()`, JSON-логи, trace-id, декоратор `@timed` |
| `exceptions.py` | Иерархия исключений (`CoreError` → Config/DB/LLM/Tool/Confirm/Autonomy/Agent/A2A) |

---

## Подсистема оценки (`eval/`)

Фреймворк «Штурм» (LLM-as-a-judge). Демон-исполнитель — сервис
[eval-runner](SERVICES.md#eval-runner); вся логика здесь:

| Модуль | Что внутри |
|--------|------------|
| `runner.py` | `EvalRunExecutor` — оркестрация прогона по стадиям, отмена |
| `schemas.py` | Pydantic-схемы: `EvalSuite` (create/update/multi/hierarchy/duplicate/no_task), статусы прогона/кейса |
| `suites.py` | Определения наборов сценариев |
| `generator.py` | LLM-генерация сценариев и пользовательских реплик |
| `judge.py` | LLM-судья: оценка ответа по критериям, панель судей |
| `normalizer.py` | Нормализация вывода агента перед судейством |
| `deterministic.py` | Не-LLM (правило-based) проверки |
| `metrics.py` | Агрегация метрик прогона (pass-rate, latency, cost) |
| `analysis.py` | Анализ результатов / диагностика |
| `export.py` | Экспорт отчётов (JSON/markdown) |
| `redaction.py` | Редактирование секретов из логов прогонов |
| `repository.py` | Доступ к eval-таблицам БД |
| `rpc_client.py` | RPC-клиент к оркестратору для прогона агента |
| `fake_tracker.py` | In-memory мок Трекера для изолированных прогонов |
| `tracker_profile.py` | Снимки состояния доски для сценариев |
| `mode.py` | Режимы прогона |
| `constants.py` | Лимиты конкуренции, модели, параметры судьи |
| `pipeline/base.py` | `PipelineContext` (конфиг прогона, репо, RPC, отмена, lock БД) |
| `pipeline/batch.py` | `BatchStagesPipeline` — генерация → реплики → прогоны → судейство (параллельно) |
| `pipeline/streaming.py` | Потоковый вариант пайплайна |

Дизайн и методология — [agent_evaluation](agent_evaluation.md).

---

## Примеры (`packages/core/examples/`)

Запускаются `uv run python packages/core/examples/<файл>`:

| Файл | Демонстрирует |
|------|---------------|
| `01_config_usage.py` | Загрузка конфига и team-overlay (auto/confirm risk) |
| `02_db_operations.py` | CRUD через async ORM |
| `03_llm_integration.py` | LLM-клиент: текст, tool-calling, стриминг, fallback |
| `04_creating_tools.py` | `@platform_tool`, регистрация инструментов с риском |
| `05_full_agent_flow.py` | Полный поток: конфиг → LLM → инструмент → Autonomy Gate |
| `06_code_first_agents.py` | Code-first агенты: `BaseAgent` + `BaseBot` + `EntryPoint` |
| `07_tracker_smoke.py` | Smoke-тест `tracker_*` против реального API (метка `smoke`) |

---

## Миграции (`packages/core/migrations/`)

Alembic-миграции схемы. Полный разбор таблиц и истории миграций —
[DATA_MODEL](DATA_MODEL.md).

---

**См. также:** [ARCHITECTURE](ARCHITECTURE.md) · [SERVICES](SERVICES.md) ·
[DATA_MODEL](DATA_MODEL.md) · [Создание агентов](agents.md)
