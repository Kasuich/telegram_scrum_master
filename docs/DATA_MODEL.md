# Модель данных

> Все сервисы работают с одной схемой PostgreSQL через ORM из
> [`packages/core/src/core/models.py`](../packages/core/src/core/models.py).
> Миграции — Alembic (`packages/core/migrations/`). Базовый класс — `Base`
> (`AsyncAttrs` + `DeclarativeBase`, типы JSONB/UUID).

Всего **38 таблиц**. Ниже сгруппированы по доменам.

---

## Организация и пользователи

| Таблица | Модель | Назначение |
|---------|--------|------------|
| `organizations` | `Organization` | Верхнеуровневая сущность, владеет командами |
| `teams` | `Team` | Команда: очередь Трекера, агент-инстансы, Telegram-инсталляции, расписания |
| `team_memberships` | `TeamMembership` | Связь пользователь ↔ команда + роли |
| `users` | `User` | Пользователь консоли (email, роль, хеш пароля, display_name) |
| `user_profiles` | `UserProfile` | Соц-профиль (аватар, должность, био, контакты, приватные поля) |
| `console_sessions` | `ConsoleSession` | Сессии веб-консоли |
| `login_challenges` | `LoginChallenge` | Коды входа / инвайты (TTL, used_at) |

---

## Агенты и исполнение

| Таблица | Модель | Назначение |
|---------|--------|------------|
| `agent_specs` | `AgentSpec` | Спецификация агента (промпт, модель, инструменты) — слой spec |
| `agent_instances` | `AgentInstance` | Инстанс агента на команду + `overlay` (team-overrides) |
| `runtime_configs` | `RuntimeConfigModel` | Runtime-настройки команды (auto/confirm risk, always_confirm_tools) |
| `actions` | `Action` | Один tool-вызов (агент, инструмент, вход/выход, статус, risk) |
| `action_feedback` | `ActionFeedback` | Оценка действия пользователем (рейтинг, комментарий) |
| `traces` | `Trace` | Состояние сессии-хода (история сообщений, чекпоинты, статус) |
| `confirms` | `Confirm` | Отложенное подтверждение Autonomy Gate (prompt, статус, кто одобрил) |

Слои Effective Config: `class defaults < agent_specs < agent_instances.overlay`
(см. [ARCHITECTURE → Effective Config](ARCHITECTURE.md#53-effective-config-промпт-без-деплоя)).

---

## Расписания

| Таблица | Модель | Назначение |
|---------|--------|------------|
| `scheduled_jobs` | `ScheduledJob` | Cron-джоба (job_name, cron_expr, payload, next_run, enabled) — основа `SchedulerDaemon` |

---

## Telegram-контур

| Таблица | Модель | Назначение |
|---------|--------|------------|
| `telegram_installations` | `TelegramInstallation` | Бот, установленный в чат/группу (team_id, chat_id, settings) |
| `telegram_chats` | `TelegramChat` | Метаданные чата (тип, заголовок, число участников) |
| `telegram_users` | `TelegramUser` | Telegram-пользователь |
| `telegram_user_links` | `TelegramUserLink` | Связь Telegram-пользователь ↔ пользователь консоли |
| `telegram_messages` | `TelegramMessage` | История сообщений |
| `telegram_updates` | `TelegramUpdate` | Принятые апдейты Bot API |
| `telegram_outbox` | `TelegramOutbox` | Очередь исходящих сообщений (статус, кнопки) — лизится шлюзом |
| `telegram_callback_tokens` | `TelegramCallbackToken` | Токены callback-кнопок |
| `telegram_import_jobs` | `TelegramImportJob` | Джобы массового импорта сообщений |
| `telegram_notification_preferences` | `TelegramNotificationPreference` | Настройки уведомлений на чат |
| `telegram_onboarding_sessions` | `TelegramOnboardingSession` | Состояние онбординга/подключения |
| `telegram_standup_polls` | `TelegramStandupPoll` | Инстанс стендап-опроса + ответы |
| `telegram_business_connections` | `TelegramBusinessConnection` | Привязка бизнес-аккаунта |

---

## Питомец и битвы

| Таблица | Модель | Назначение |
|---------|--------|------------|
| `pet_states` | `PetState` | Питомец пользователя (xp, level, mood, species_id, состояние) |
| `pet_battles` | `PetBattle` | Результат битвы/дуэли (режим, участники, победитель, лог) |

---

## Встречи

| Таблица | Модель | Назначение |
|---------|--------|------------|
| `meetings` | `Meeting` | Запись встречи (ссылка, статус, участники) |
| `meeting_artifacts` | `MeetingArtifact` | Артефакт встречи (аудио/текст/саммари) |
| `transcripts` | `Transcript` | Расшифровка (язык, текст, сегменты со спикерами) |

---

## Оценка качества («Штурм»)

| Таблица | Модель | Назначение |
|---------|--------|------------|
| `eval_runs` | `EvalRun` | Прогон оценки (suite, статус, конфиг, результат) |
| `eval_cases` | `EvalCase` | Кейс прогона (сценарий, реплика, ожидаемое) |
| `eval_case_results` | `EvalCaseResult` | Результат кейса (ответ агента, оценка судьи, фидбек) |
| `eval_metrics` | `EvalMetric` | Агрегированная метрика прогона |
| `eval_events` | `EvalEvent` | Журнал событий прогона |

---

## История миграций (`migrations/versions/`)

| Файл | Что добавляет |
|------|---------------|
| `20260101_0000_initial_schema.py` | Базовая схема: организации, пользователи, команды, агенты, actions/traces/confirms, scheduled_jobs |
| `20260606_0001_add_telegram_runtime_tables.py` | Telegram runtime: updates, outbox, callback_tokens, import_jobs |
| `20260606_0001_meeting_capture.py` | Meeting Capture: meetings, meeting_artifacts, transcripts |
| `20260609_0003_telegram_auth.py` | Telegram auth: user_links, onboarding_sessions, business_connections |
| `20260609_0004_standup_polls.py` | Стендап-опросы (`telegram_standup_polls`) |
| `20260610_0005_pm_agent_model.py` | Модель агента в spec/instance |
| `20260610_0006_meeting_text_artifacts.py` | Текстовые артефакты встреч |
| `20260611_0007_user_profiles.py` | Профили пользователей (`user_profiles`) |
| `20260611_0008_pet_states.py` | Состояния питомца (`pet_states`) |
| `20260612_0009_pet_species.py` | Виды питомца (`species_id`) |
| `20260612_0010_pet_battles.py` | Битвы питомцев (`pet_battles`) |
| `20260613_0010_eval_tables.py` | Таблицы «Штурма»: eval_runs/cases/case_results/metrics/events |

### Применение миграций

```bash
cd packages/core
uv run alembic upgrade head        # накатить всё
uv run alembic revision --autogenerate -m "описание"   # новая миграция
uv run alembic history             # история
```

> В Docker-стенде схема при первом старте может подниматься через
> `create_all_tables()` (`core/db.py`). На проде применяйте Alembic, чтобы новые
> колонки доезжали до уже существующих таблиц.

---

**См. также:** [CORE_LIBRARY](CORE_LIBRARY.md) · [ARCHITECTURE](ARCHITECTURE.md) ·
[Конфигурация](CONFIGURATION.md)
