# Сервисы и приложения

> Покомпонентный справочник по всем сервисам монорепозитория. Высокоуровневая
> картина — [ARCHITECTURE](ARCHITECTURE.md); общая библиотека — [CORE_LIBRARY](CORE_LIBRARY.md).

Все сервисы — FastAPI-приложения на Python 3.12, общий код берут из `packages/core`,
конфигурируются через переменные окружения (см. [CONFIGURATION](CONFIGURATION.md)),
экспонируют `GET /health` и (где применимо) `GET /metrics`.

| Сервис | Порт | Entry point |
|--------|------|-------------|
| [pm-orchestrator](#pm-orchestrator) | 8001 | `services/pm-orchestrator/src/pm_orchestrator/rpc.py` |
| [platform-api](#platform-api) | 8000 | `services/platform-api/src/platform_api/main.py` |
| [console-api](#console-api) | 8002 | `services/console-api/src/console_api/main.py` |
| [meeting-capture](#meeting-capture) | 8003 | `services/meeting-capture/src/meeting_capture/main.py` |
| [eval-runner](#eval-runner) | 8004 | `services/eval-runner/src/eval_runner/main.py` |
| [telegram-gateway](#telegram-gateway) | 8080 | `services/telegram-gateway/src/telegram_gateway/main.py` |
| [web-ui](#web-ui) | 5173→80 | `apps/web-ui/src/main.tsx` |

---

## pm-orchestrator

**Роль.** «Мозг» платформы. Автоматически находит агентов, гоняет ReAct-цикл,
применяет Autonomy Gate, исполняет инструменты, держит Scheduler daemon, отдаёт
всё это по JSON-RPC. Единственный сервис, который ходит в LLM и в Tracker MCP.

**Интерфейс — JSON-RPC 2.0, `POST /rpc`:**

| Метод | Параметры | Результат |
|-------|-----------|-----------|
| `list_agents` | — | `[{name, description}]` |
| `invoke` | `agent, message, session_id, context?` | `AgentResult` |
| `resume` | `confirm_id, approved` | `AgentResult` |
| `agent_tools` | `agent` | список инструментов агента с риск-метаданными |
| `get_actions` | `session_id?, limit?` | лог последних tool-вызовов |

Плюс `GET /health` (статус + список агентов) и `GET /metrics` (Prometheus).

**Связи:** PostgreSQL (agent_specs/instances, traces, actions, confirms,
scheduled_jobs); Tracker MCP gateway; OpenRouter / Yandex Cloud (LLM);
`meeting-capture` (через инструменты записи встреч).

**Структура `src/pm_orchestrator/`:**

| Файл | Назначение |
|------|------------|
| `rpc.py` | JSON-RPC сервер, lifespan, запуск/остановка `SchedulerDaemon`, регистрация методов |
| `orchestrator.py` | `OrchestratorService`: автодискавери агентов, `invoke`/`resume`, загрузка effective config, персист в БД |
| `__main__.py` | Точка входа uvicorn |
| `agents/pm_agent.py` | Основной PM-агент (Tracker MCP + `tracker_*` + backlog + meeting + scheduling) |
| `agents/audit_agent.py` | Агент аудита доски (`audit_board_digest`), gemini-3.1-pro |
| `agents/meeting_summarizer.py` | Суммаризатор транскриптов (без Трекер-инструментов) |
| `tools/call_agent.py` | Инструмент `call_agent` — делегирование агент→агент, ограничение глубины |
| `tools/schedule_task.py` | Инструмент `schedule_task` — создание cron-`ScheduledJob` |
| `tools/meeting_capture.py` | Инструменты `schedule_meeting_bot` / `get_meeting_transcript` |

Агенты подхватываются автоматически: при старте оркестратор импортирует все модули
`agents/`, находит подклассы `BaseAgent` и регистрирует. См. [ADDING_AGENTS](ADDING_AGENTS.md).

---

## platform-api

**Роль.** Тонкий HTTP-BFF (backend-for-frontend) поверх оркестратора: переводит
HTTP в JSON-RPC, генерирует маршруты под каждого агента, обслуживает Telegram
bridge (приём апдейтов от шлюза и выдача исходящих сообщений).

**HTTP-эндпоинты:**

| Метод/путь | Назначение |
|------------|------------|
| `GET /agents` | список агентов |
| `GET /agents/{agent}/tools` | инструменты агента с риск-метаданными |
| `POST /agents/{agent}/chat` | сообщение конкретному агенту |
| `POST /agents/{agent}/confirm/{id}` | подтвердить/отклонить отложенный tool-вызов |
| `POST /chat`, `POST /confirm/{id}` | шорткаты к дефолтному агенту (`pm_agent`) |
| `GET /actions` | лог tool-вызовов (фильтр по сессии) |
| `GET /health`, `GET /metrics` | здоровье + Prometheus |
| `/internal/telegram/v1/*` | Telegram bridge: ingest апдейтов, лизинг outbox |

**Структура `src/platform_api/`:**

| Файл | Назначение |
|------|------------|
| `main.py` | FastAPI-приложение, маршруты агентов, `ChatRequest`/`ChatResponse` |
| `rpc_client.py` | JSON-RPC клиент оркестратора (in-process в dev, HTTP в Docker) |
| `telegram_bridge.py` | Роутер bridge: приём апдейтов шлюза, отдача outbox |
| `telegram_auth.py` | HMAC-подписи / nonce-валидация запросов шлюза и initData Mini App |
| `telegram_import.py` | Импорт Telegram-сообщений как контекста агента |
| `telegram_media.py` | Обработка медиа (файлы, фото, документы) |

**Связи:** `pm-orchestrator` (`ORCHESTRATOR_URL`), PostgreSQL, `telegram-gateway`
(через bridge). Bridge включается флагом `TELEGRAM_BRIDGE_ENABLED`.

---

## console-api

**Роль.** Бэкенд веб-консоли и Telegram Mini App. Самый «продуктовый» сервис:
аутентификация, профили, личная доска и статистика из Трекера, питомец «Скрамик»,
магазин, битвы, конфигурация агентов, управление расписаниями, аудит действий,
тестовый прогон агента.

**Группы эндпоинтов:**

- **Auth** — `POST /auth/login` (email+пароль), `POST /auth/code/request` +
  `/auth/code/verify` (вход по коду), `/auth/logout`, `POST /auth/telegram/webapp`
  (Mini App по initData), `GET /auth/me`.
- **Профили** — `GET/PATCH /me/profile`, `POST /me/avatar`,
  `GET /users/{id}/profile`, `GET /users/{id}/avatar`.
- **Личная доска / статистика** — `GET /me/board` (канбан из Трекера),
  `GET /me/stats` (throughput, lead time, распределение статусов).
- **Питомец** — `GET /me/pet`, `GET /users/{id}/pet`, dev-ручки
  `grant-xp`/`set-species`/`reset` (под флагом `PET_DEV_TOOLS`).
- **Магазин** — `GET /me/pet/shop`, `POST /me/pet/buy`, `PUT /me/pet/equip`.
- **Битвы** — `POST /me/battle/team` (royale + картинка + пост в Telegram),
  `GET /me/battle/leaderboard`, `POST /me/battle/duel/{id}`, `GET /me/battle/duels`.
- **Агенты** — `GET /agents`, `GET /agents/{name}/config`,
  `PATCH /agents/{name}/spec` (промпт/модель), `PATCH /agents/{name}/overlay`
  (enabled/автономия), `GET/PATCH /agents/{name}/tools`.
- **Расписания** — `GET/PATCH/DELETE` по `scheduled-jobs` команды.
- **Аудит действий** — `GET /actions` (фильтры status/risk/agent),
  `GET /actions/{id}`, `POST /actions/{id}/feedback`, `GET /confirms/{id}` +
  `POST /confirms/{id}/decide`.
- **Команда** — здоровье команды, состав, запуск аудита (`audit_agent`).
- **Eval / Playground** — тестовый чат агента и прогоны по доске.

**Структура `src/console_api/`:**

| Файл | Назначение |
|------|------------|
| `main.py` | FastAPI-приложение со всеми маршрутами консоли |
| `security.py` | Хеширование паролей/сессий/кодов (PBKDF2, HMAC) |
| `eval_routes.py` | Маршруты тестового прогона агента по доске/набору промптов |

**Связи:** PostgreSQL (пользователи, сессии, профили, питомцы, битвы, действия,
расписания); Трекер REST (`core.tracker`) для личной доски/статистики;
`platform-api` для списка/конфига агентов. Раздаётся фронтенду через nginx
`web-ui` (`/api/` → `console-api:8002`).

---

## meeting-capture

**Роль.** Тяжёлый сервис записи встреч Telemost. Бот заходит по ссылке как обычный
гость (Chromium/Playwright), пишет экран и звук (FFmpeg), сохраняет артефакты в
S3/локально и при наличии SpeechKit строит транскрипт со спикерами и таймкодами,
затем триггерит `meeting_summarizer`.

**Эндпоинты:** `POST /meetings` (создать запись по `telemost_url`),
`GET /meetings/{id}`, `POST /meetings/{id}/stop`, `POST /meetings/{id}/transcribe`
(пере-STT + опционально саммари), `GET /meetings/{id}/transcript`,
`GET /meetings/{id}/transcripts/summary`, `GET /health`.

**Структура `src/meeting_capture/`:**

| Файл | Назначение |
|------|------------|
| `main.py` | FastAPI-приложение, CRUD встреч |
| `dispatcher.py` | `MeetingDispatcher`: жизненный цикл встречи, очередь задач STT/саммари, TTL аудио |
| `recorder.py` | Бот-гость: вход в Telemost, запись экрана/звука |
| `bot.py` | Клиент Telemost (управление сессией браузера) |
| `transcription.py` | Интерфейс к SpeechKit (STT) |
| `storage.py` | Абстракция объектного хранилища (S3/локально) |
| `repository.py` | Запросы к БД (встречи, транскрипты, сегменты) |
| `telegram_outbox.py` | Постановка итогового отчёта в `telegram_outbox` |
| `schemas.py` | Pydantic-DTO запросов/ответов |
| `url.py` | Нормализация ссылок Telemost |
| `config.py` | Настройки сервиса |

**Связи:** PostgreSQL, S3 (`S3_*`), SpeechKit (`SPEECHKIT_API_KEY`),
`pm-orchestrator` (вызов `meeting_summarizer`). См. [meeting_capture](meeting_capture.md).

---

## eval-runner

**Роль.** Фоновый демон фреймворка оценки «Штурм». Не имеет «продуктового» API —
поднимает `EvalRunnerDaemon`, который берёт из БД поставленные в очередь прогоны,
гоняет агента через оркестратор (на fake-трекере или реальной доске при
`ALLOW_REAL_TRACKER_EVAL=true`), судит ответы и пишет метрики/диагностику в БД.

**Эндпоинты:** только `GET /health`.

**Структура `src/eval_runner/`:**

| Файл | Назначение |
|------|------------|
| `main.py` | FastAPI-приложение, lifespan, запуск демона, `/health` |
| `daemon.py` | `EvalRunnerDaemon`: опрос очереди прогонов и оркестрация пайплайна |

Вся логика оценки (генерация сценариев, судья, метрики, пайплайн) живёт в
`core/eval/` — см. [CORE_LIBRARY → eval](CORE_LIBRARY.md#подсистема-оценки-eval).
UI прогонов — страница `/eval` в консоли. Связи: PostgreSQL, `pm-orchestrator`.

---

## telegram-gateway

**Роль.** Standalone-шлюз, разворачивается на отдельном публичном сервере с TLS.
Принимает вебхук Telegram, кладёт апдейты в персистентный spool (SQLite), пробрасывает
их в основной стек по HMAC-подписанному bridge, и в обратную сторону — лизит
`telegram_outbox` и доставляет сообщения в Telegram с ретраями и dead-letter.

**Эндпоинты:** `POST /webhook` (вебхук, проверка `X-Telegram-Bot-Api-Secret-Token`),
`GET /health/live`, `GET /health/ready`, `GET /metrics`, внутренние
`/internal/installations/resolve` и т.п.

**Структура `src/telegram_gateway/`:**

| Файл | Назначение |
|------|------------|
| `main.py` | Фабрика приложения, webhook, health, metrics |
| `runtime.py` | `GatewayRuntime`: spool, воркеры доставки, лизинг outbox, ретраи |
| `bot_api.py` | Клиент Telegram Bot API (sendMessage/sendPhoto…) |
| `bridge.py` | Клиент bridge основного сервера (HMAC nonce) |
| `spool.py` | Персистентный spool апдейтов/сообщений (SQLite) |
| `formatting.py` | Форматирование сообщений Telegram |
| `streaming.py` | Поддержка стриминга/чанков ответа |
| `settings.py` | `GatewaySettings` |
| `__main__.py` | CLI-точка входа |

**Связи:** Telegram Bot API; bridge основного сервера (`MAIN_BRIDGE_URL`,
`TELEGRAM_BRIDGE_HMAC_*`). Деплой и nginx/TLS/WireGuard —
[DEPLOYMENT](DEPLOYMENT.md), эксплуатация — [runbook](runbooks/telegram-gateway-runbook.md).

---

## web-ui

**Роль.** Веб-консоль (React SPA) и Telegram Mini App. Собирается Vite, раздаётся
nginx, который проксирует `/api/` → `console-api:8002`.

**Стек:** React 18 + TypeScript, Vite, Tailwind, react-router-dom,
TanStack Query/Table, recharts, lucide-react.

**Ролевая модель:** `user` ⊂ `teamlead` ⊂ `developer` — доступ к маршрутам
наследуется вниз по цепочке.

**Страницы (`src/pages/`):**

| Маршрут | Мин. роль | Страница |
|---------|-----------|----------|
| `/` | user | `HomePage` — приветствие, плитки по роли |
| `/board` | user | `BoardPage` — личный канбан из Трекера + графики |
| `/pet` | user | `PetPage` — питомец, характеристики, магазин |
| `/profile`, `/users/:id` | user | `ProfilePage` — карточка профиля |
| `/team` | teamlead | `TeamLeadPage` — здоровье команды, аудит, расписания, состав |
| `/people` | developer | `PeoplePage` — каталог пользователей |
| `/dev` | developer | `DevPage` — конфиг агентов, инструменты, трейсы |
| `/admin` | developer | `AdminPage` — журнал действий, confirm, фидбек |
| `/playground` | developer | `PlaygroundPage` — интерактивный чат с агентом |
| `/eval`, `/eval/new`, `/eval/:id`, `/eval/:id/cases/:caseId` | developer | «Штурм» — прогоны оценки |
| `/tg/*` | user | Telegram Mini App (отдельная оболочка) |

**Компоненты (`src/components/`):** `Badge` (risk/status), `Markdown`
(лёгкий рендер без зависимостей), `TraceTimeline` (таймлайн шагов трейса),
`AgentConfigPanel` (редактор промпта/модели/автономии), `AgentToolsPanel`
(вкл/выкл инструментов + confirm), `AuditReport` (отчёт аудита), `shturm`
(пресентационные утилиты eval-UI).

**Telegram Mini App (`src/tg/`):** оболочка `TgApp` с авторизацией по `initData`
и вкладками — `TgPetScreen` (питомец), `TgBattleScreen` (битвы/дуэли),
`TgTeamScreen` (здоровье команды, teamlead+), `TgMoreScreen`; обёртка SDK в
`telegram.ts`.

**API-клиент (`src/lib/api.ts`):** единая точка вызовов к `console-api`
(`VITE_CONSOLE_API_URL` или `/api`), с типами всех сущностей и `localizeError()`
для русских сообщений об ошибках. Логотипы агентов — `src/lib/agentLogos.ts`
(`public/agents/*.png`).

**Сборка/раздача:** многостадийный `Dockerfile` (Node 22 build → nginx 1.27),
`nginx.conf` с SPA-fallback и прокси `/api/`.

---

**См. также:** [ARCHITECTURE](ARCHITECTURE.md) · [CORE_LIBRARY](CORE_LIBRARY.md) ·
[DATA_MODEL](DATA_MODEL.md) · [CONFIGURATION](CONFIGURATION.md)
