# Конфигурация (переменные окружения)

> Все сервисы конфигурируются через env. Источник для основного стека —
> файл `.env.test` (копия [`.env.example`](../.env.example)), для шлюза —
> [`config/telegram-gateway/.env.example`](../config/telegram-gateway/.env.example).
> Секреты в репозиторий не коммитятся (см. `.gitignore`).

Минимальный набор для локального старта — в корневом [README](../README.md#3-настроить-переменные).
Ниже — полный справочник по группам.

---

## База данных

| Переменная | По умолч. | Описание |
|------------|-----------|----------|
| `DB_USER` | `pm_agent_test` | Пользователь PostgreSQL |
| `DB_NAME` | `pm_agent_test` | Имя БД |
| `DB_PASSWORD` | — | Пароль PostgreSQL |
| `DATABASE_URL` | собирается в compose | `postgresql+asyncpg://…` для каждого сервиса |
| `DEFAULT_TEAM_ID` | `00000000-…-0001` | Команда по умолчанию для персиста actions/traces/confirms |

## LLM-провайдеры

| Переменная | Описание |
|------------|----------|
| `OPENROUTER_API_KEY` | Ключ OpenRouter — основной LLM агентов (Gemini) |
| `YC_API_KEY` | Yandex Cloud API key (Responses API `gpt-oss-120b`, SpeechKit) |
| `YC_FOLDER_ID` | Yandex Cloud folder ID |

## Яндекс Трекер

| Переменная | По умолч. | Описание |
|------------|-----------|----------|
| `TRACKER_TOKEN` | — | OAuth-токен Трекера |
| `TRACKER_ORG_ID` | — | ID организации |
| `TRACKER_ORG_TYPE` | `360` | `cloud` или `360` |
| `TRACKER_QUEUE` | `TEST` | Ключ очереди по умолчанию (напр. `DARKHORSE`) |
| `TRACKER_MCP_URL` | — | URL Tracker MCP gateway (HTTP+SSE) — инструменты агента |
| `TRACKER_MCP_TOKEN` | — | Токен приватного MCP gateway (пусто для публичного) |
| `TRACKER_MCP_TIMEOUT` | `60` | Таймаут MCP-запроса, сек |

Настройка MCP-сервера — [TRACKER_MCP_SETUP](TRACKER_MCP_SETUP.md), справочник очереди —
[tracker_queue_DARKHORSE](tracker_queue_DARKHORSE.md).

## Приложение

| Переменная | По умолч. | Описание |
|------------|-----------|----------|
| `ENVIRONMENT` | `test` | `test` / `production` |
| `LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `RUNTIME_SKIP_TOOL_CONFIRM` | `false` | Пропуск Autonomy Gate (только тесты!) |
| `CHAT_MAX_MESSAGE_LENGTH` | `100000` | Лимит длины сообщения (для саммари встреч) |
| `ORCHESTRATOR_URL` | `http://pm-orchestrator:8001` | Адрес оркестратора для platform-api/eval/meeting |
| `PLATFORM_API_URL` | `http://platform-api:8000` | Адрес platform-api для console-api |
| `MEETING_CAPTURE_URL` | `http://meeting-capture:8003` | Адрес meeting-capture |

## Веб-консоль (console-api)

| Переменная | По умолч. | Описание |
|------------|-----------|----------|
| `CONSOLE_ADMIN_EMAIL` | `admin@example.com` | Дефолтный админ |
| `CONSOLE_ADMIN_PASSWORD` | `admin` | Пароль админа |
| `CONSOLE_LOGIN_CODE_SECRET` | — | HMAC-секрет кодов входа |
| `CONSOLE_CORS_ORIGINS` | `localhost:5173,…` | Разрешённые origin'ы CORS |
| `CONSOLE_AVATAR_DIR` | `/data/avatars` | Каталог аватаров |
| `CONSOLE_SECURE_COOKIES` | `false` | `Secure`-cookie (true за HTTPS, нужно для Mini App) |
| `ALLOW_REAL_TRACKER_EVAL` | `false` | Разрешить eval на реальной доске |

## Telegram (Mini App на основном сервере)

| Переменная | По умолч. | Описание |
|------------|-----------|----------|
| `TELEGRAM_BOT_TOKEN` | — | Токен бота; валидирует initData Mini App на console-api |
| `MINI_APP_URL` | `https://…/tg` | Публичный URL Mini App (кнопка меню чата) |
| `TG_WEBAPP_DEV` | `false` | Dev: пропуск проверки подписи initData |
| `TELEGRAM_CHAT_ID` | — | Чат для диагностических сообщений/алертов |

## Telegram bridge (platform-api ↔ gateway)

| Переменная | По умолч. | Описание |
|------------|-----------|----------|
| `TELEGRAM_BRIDGE_ENABLED` | `false` | Включить bridge на platform-api |
| `TELEGRAM_BRIDGE_HMAC_KEYS` | — | HMAC-ключи (`key_id:secret,…`) |
| `TELEGRAM_BRIDGE_NONCE_TTL` | `300` | Окно защиты от повтора, сек |
| `TELEGRAM_OUTBOX_LEASE_SECONDS` | `60` | Лизинг исходящих сообщений |

Переменные самого шлюза (`telegram-gateway`) см. в
[`config/telegram-gateway/.env.example`](../config/telegram-gateway/.env.example)
и [DEPLOYMENT → telegram-gateway](DEPLOYMENT.md): `TELEGRAM_WEBHOOK_SECRET`,
`TELEGRAM_WEBHOOK_BASE_URL`, `MAIN_BRIDGE_URL`, `TELEGRAM_BRIDGE_HMAC_KEY_ID`,
`GATEWAY_*` (spool/ретраи/лимиты).

## Дайджесты, стендапы, напоминания (pm-orchestrator)

| Переменная | По умолч. | Описание |
|------------|-----------|----------|
| `DAILY_DIGEST_ENABLED` | `true` | Часовой дайджест |
| `DAILY_DIGEST_CRON_EXPR` | `0 * * * *` | Cron дайджеста |
| `DAILY_DIGEST_TIMEZONE` | `Europe/Moscow` | TZ |
| `DAILY_DIGEST_TELEGRAM_CHAT_ID` | — | Чат для дайджеста |
| `DAILY_DIGEST_IN_PROGRESS_STATUSES` | `In Progress,В работе` | Статусы «в работе» |
| `DAILY_DIGEST_MAX_*` | 10/30/20 | Лимиты секций/спринта/сообщений |
| `STANDUP_POLL_ENABLED` | `true` | Стендап-опрос |
| `STANDUP_POLL_CRON_EXPR` | `50 * * * *` | Cron опроса |
| `STANDUP_POLL_LEAD_MINUTES` | `10` | Лид-тайм |
| `STANDUP_POLL_MAX_ISSUES_PER_MEMBER` | `20` | Лимит задач на участника |
| `DEADLINE_REMINDER_ENABLED` | `true` | Напоминания о дедлайнах |
| `DEADLINE_REMINDER_CRON_EXPR` | `0 * * * *` | Cron напоминаний |
| `DEADLINE_REMINDER_SOON_DAYS` | `3` | Сколько дней до дедлайна считать «скоро» |
| `DEADLINE_REMINDER_NOTIFY_ASSIGNEES` | `true` | Личные DM исполнителям |
| `DEADLINE_REMINDER_NOTIFY_LEAD` | `true` | Сводка лиду |
| `DEADLINE_REMINDER_LEAD_ROLES` | `lead,admin` | Роли лида |
| `DEADLINE_REMINDER_LEAD_LOGIN` | — | Логин лида для сводки |

## Питомец «Скрамик» (console-api)

| Переменная | По умолч. | Описание |
|------------|-----------|----------|
| `PET_FAST_MODE` | `true` | Быстрый набор уровней (для MVP); `false` — прод-кривая |
| `PET_DEV_TOOLS` | `false` | Dev-ручки grant-xp/set-species/reset + панель |
| `PET_XP_PER_RESOLVED`, `PET_COINS_PER_RESOLVED`, `PET_LEVEL_CURVE_BASE`, `PET_LEVEL_CURVE_EXP` | — | Точечные оверрайды кривой |

## Meeting Capture

| Переменная | По умолч. | Описание |
|------------|-----------|----------|
| `SPEECHKIT_API_KEY` | — | Ключ Yandex SpeechKit (STT) |
| `S3_ENDPOINT` / `S3_BUCKET` / `S3_ACCESS_KEY` / `S3_SECRET_KEY` / `S3_REGION` | — / `ru-central1` | Хранилище артефактов встреч |
| `CAPTURE_BOT_DISPLAY_NAME` | `PM Assistant (recording)` | Имя бота в Telemost |
| `CAPTURE_JOIN_TIMEOUT_SEC` | `900` | Таймаут входа в встречу |
| `CAPTURE_MAX_DURATION_SEC` | `14400` | Макс. длительность записи |
| `CAPTURE_AUDIO_TTL_DAYS` | `7` | TTL аудио |

## Мониторинг (Grafana)

| Переменная | Описание |
|------------|----------|
| `GRAFANA_USER` / `GRAFANA_PASSWORD` | Логин Grafana |
| `GRAFANA_URL` | Базовый URL (напр. за `/grafana`) |
| `GRAFANA_SERVE_FROM_SUB_PATH` | Раздача из под-пути |

## Фронтенд (build-time, Vite)

| Переменная | Описание |
|------------|----------|
| `VITE_CONSOLE_API_URL` | База API (по умолчанию `/api` → прокси на console-api) |
| `VITE_TG_DEV_INITDATA` | Мок initData для `/tg` в браузере (dev) |

---

**См. также:** [README → настройка](../README.md#3-настроить-переменные) ·
[DEPLOYMENT](DEPLOYMENT.md) · [ARCHITECTURE](ARCHITECTURE.md)
