# План разработки — базовый функционал

> Только базовый функционал, без киллер-фич. Цель — рабочий **вертикальный срез**
> платформы и параллельная работа 4 разработчиков с минимумом блокировок.

---

## Где мы сейчас (Фаза 0 — готово)

- ✅ Монорепо (uv workspaces), `packages/core` + `services/*`
- ✅ Docker Compose (test) + Postgres
- ✅ CI/CD: lint + тесты + авто-деплой на тест-VPS при push в `develop`
- ✅ Скелеты: `platform-api` (health), `pm-orchestrator` (заглушка)

Сейчас сервисы пустые. Задача — наполнить их базовой логикой.

---

## Цель: вертикальный срез

Один сквозной сценарий, доказывающий что платформа работает:

```
Пользователь → "заведи задачу: починить логин, срочно"
   → Агент (LLM рассуждает)
   → вызывает tracker_create_issue
   → Autonomy Gate: medium-risk → confirm в чат
   → Пользователь подтверждает
   → задача появляется в Трекере
   → действие залогировано в actions + trace
   → ответ пользователю
```

Когда это работает end-to-end — базовый функционал готов. Всё остальное (доп. агенты, A2A-сеть, киллер-фича, дашборд) строится поверх.

**Сознательно отложено:** networked A2A (пока один агент с in-process тулзами), Meeting Capture, Correspondence/Analytics агенты, киллер-фича, полноценный UI.

---

## Разбор Яндекс Трекера

### Покрывает ли бесплатный тариф наши нужды?

**Да.** Бесплатный тариф — до 5 пользователей, вас 4. API доступен. Этого хватает для разработки и теста.

⚠️ Ограничения: при 6+ пользователях нужен платный тариф (от ~258 ₽/чел/мес); при нуле на счету или смене тарифа Трекер уходит в режим **«только чтение»** (API на запись отвалится).

### Как устроен Трекер

```
Организация (Яндекс 360 или Yandex Cloud Org)
  └── Очередь (Queue, ключ напр. "TEST")    ← аналог проекта/доски
        └── Задачи (Issues)
              ├── summary, description
              ├── type (task/bug), priority, assignee
              ├── status (workflow: Open → In Progress → Done)
              ├── tags, components, links (blocks/duplicates)
              └── комментарии
```

### API

| Операция | Запрос |
|---|---|
| База | `https://api.tracker.yandex.net/v3/` |
| Создать задачу | `POST /v3/issues/` (нужны `summary` + `queue`) |
| Получить задачу | `GET /v3/issues/{key}` |
| Обновить | `PATCH /v3/issues/{key}` |
| Сменить статус | `POST /v3/issues/{key}/transitions/{id}/_execute` |
| Поиск | `POST /v3/issues/_search` |
| Создать очередь | `POST /v3/queues/` |

Есть официальный Python-клиент `yandex_tracker_client`, но для async-стека лучше тонкая обёртка над REST через `httpx`.

### Аутентификация — что выбрать

| Схема | Заголовки | Плюс | Минус |
|---|---|---|---|
| **OAuth + Яндекс 360** ✅ | `Authorization: OAuth <token>` + `X-Org-ID` | Токен долгоживущий, просто получить | Нужна Яндекс 360 организация |
| IAM + Yandex Cloud Org | `Authorization: Bearer <token>` + `X-Cloud-Org-ID` | Родной для Cloud | IAM живёт ≤12ч; сервисный аккаунт = тикет в саппорт |

**Рекомендация для хакатона:** OAuth + Яндекс 360 (создаётся бесплатно), токен в GitHub Secrets как `TRACKER_TOKEN` + `TRACKER_ORG_ID`.

### Setup-чеклист (делается один раз, до старта треков)

1. Создать организацию (Яндекс 360 для бизнеса — бесплатно до 5 чел)
2. Подключить Трекер, пригласить 4 разрабов
3. Создать тестовую очередь (напр. ключ `TEST`) — через UI или `POST /v3/queues/`
4. Получить OAuth-токен (создать OAuth-приложение → выдать токен)
5. Скопировать `X-Org-ID` (Администрирование → Организации → ID)
6. Положить `TRACKER_TOKEN`, `TRACKER_ORG_ID` в GitHub Secrets + локальный `.env.test`

---

## Контракты (Задача 0 — делаем вместе в день старта)

Ключ к параллельной работе: **сначала договариваемся об интерфейсах**, потом каждый кодит против контракта, а не против чужой реализации. 4 трека работают независимо на моках.

```python
# 1. Tool contract (Track A пишет тулзы против этого)
@platform_tool(name="tracker_create_issue", risk="medium", scopes=["tracker:write"])
async def tracker_create_issue(queue: str, summary: str, ...) -> dict: ...

ToolRegistry.get(name) -> Tool
ToolRegistry.list() -> list[Tool]   # для передачи в LLM

# 2. LLM contract (Track C кодит ReAct против этого)
async def complete(messages: list[Msg], tools: list[ToolSpec]) -> LLMResponse
# LLMResponse.tool_calls: list[ToolCall] | LLMResponse.content: str

# 3. DB-модели (Track B владеет, остальные читают/пишут)
actions(id, team_id, tool_name, input, output, risk, status, trace_id, created_at)
traces(id, session_id, steps: jsonb, created_at)
confirms(id, action_id, prompt, status, answer, created_at)

# 4. Agent entry (Track D дёргает это)
async def invoke(message: str, session_id: str) -> AgentResult
# AgentResult.reply: str | AgentResult.pending_confirm: Confirm | None

# 5. HTTP contract (Track D реализует, демо против этого)
POST /chat        {message, session_id} -> {reply, pending_confirm?}
POST /confirm/{id} {approved: bool}     -> {reply}
GET  /actions                            -> [action, ...]
GET  /traces/{id}                        -> {steps: [...]}
```

---

## 4 параллельных трека

```mermaid
graph TB
    T0["🤝 Задача 0: контракты + Tracker setup<br/>(все вместе, день 1)"]

    subgraph tracks["Параллельно"]
        A["🅰️ Tracker Integration<br/>(Dev A)"]
        B["🅱️ Core Platform<br/>(Dev B)"]
        C["🅲 Agent + Autonomy<br/>(Dev C)"]
        D["🅳 Entry + Observability<br/>(Dev D)"]
    end

    INT["🔗 Интеграция среза<br/>(все, в конце)"]

    T0 --> A & B & C & D
    A -.тулзы.-> INT
    B -.core.-> INT
    C -.агент.-> INT
    D -.api/chat.-> INT

    B -.->|"интерфейсы<br/>в день 1"| A & C & D

    style T0 fill:#f4a460,color:#000
    style INT fill:#90EE90,color:#000
```

### 🅰️ Track A — Tracker Integration (Dev A)

Самый независимый трек, тестируется на **реальной** очереди.

- [ ] `TrackerClient` — async-обёртка над REST v3 (`httpx`): create/get/update/transition/search
- [ ] Тулзы `tracker_*` (через `@platform_tool`): `create_issue`, `get_issue`, `update_issue`, `move_issue`, `comment`, `search`
- [ ] Проставить корректные `risk`-уровни (create=medium, update/move/comment=low, get/search=read)
- [ ] Smoke-скрипт: создать/прочитать/закрыть задачу в реальной очереди `TEST`
- [ ] Обработка ошибок API (401, 404, режим «только чтение»)

**Зависит от:** контракт `@platform_tool` (день 1). До него — пишет `TrackerClient` (он самодостаточен).
**Отдаёт:** импортируемые тулзы + рабочая интеграция с Трекером.

### 🅱️ Track B — Core Platform + Monitoring Infrastructure (Dev B)

Фундамент. **Приоритет — выкатить интерфейсы-заглушки в день 1**, чтобы разблокировать остальных.

**Core Platform:**
- [x] `config.py` — pydantic-settings (DATABASE_URL, YC_API_KEY, YC_FOLDER_ID, TRACKER_TOKEN, TRACKER_ORG_ID)
- [x] `db.py` — async engine + session (SQLAlchemy + asyncpg)
- [x] Миграции (Alembic): все таблицы платформы (11 таблиц, 3 ENUM, 8 индексов)
- [x] `llm.py` — обёртка YandexGPT (httpx), интерфейс `complete(messages, tools)`, streaming, retry
- [x] `tools.py` — `@platform_tool` декоратор + `ToolRegistry` (singleton, schema generation, validation)
- [x] `exceptions.py` — иерархия исключений с exception chaining
- [x] `logging.py` — structured JSON logging, ContextVar trace_id, `@timed` декоратор
- [x] `prompts.py` — system prompt PM-агента, форматирование тулзов и confirm-запросов
- [x] Unit-тесты: `test_config`, `test_db`, `test_llm` (28), `test_models` (89), `test_tools`
- [x] Интеграционные тесты: `test_full_system` (27)
- [x] Примеры: `examples/01–05_*.py`
- [x] `.env.example`
- [ ] Проверить доступ к модели в Yandex AI Studio (тестовый вызов на реальном ключе)

**Метрики (`core/metrics.py`):**
- [ ] Prometheus-счётчики и гистограммы:
  - `llm_requests_total{model, status}`, `llm_latency_seconds{model}`, `llm_tokens_total{model, type}`
  - `tool_executions_total{tool_name, risk, status}`, `tool_latency_seconds{tool_name}`
  - `external_requests_total{service, status_code}`, `external_latency_seconds{service}`
  - `db_pool_checked_out` gauge
- [ ] Хелпер `@track_metrics` — оборачивает async-функцию, пишет latency + status автоматически
- [ ] Инструментировать `llm.py` и `tools.py` через `@track_metrics`

**Мониторинг-инфраструктура (не зависит от других треков):**
- [ ] `docker-compose.monitoring.yml` — оверлей с сервисами мониторинга:
  - `prometheus` — scrape platform-api, pm-orchestrator, cadvisor, node-exporter
  - `grafana` — provisioning дашбордов и datasource из файлов
  - `alertmanager` — маршрутизация алертов в Telegram Bot
  - `cadvisor` — метрики контейнеров (CPU/Memory/Network/Disk per container)
  - `node-exporter` — метрики хоста (CPU, RAM, disk I/O, network)
- [ ] `monitoring/prometheus.yml` — scrape-конфиг
- [ ] `monitoring/alertmanager.yml` — Telegram webhook (шаблон, токен бота через env)
- [ ] `monitoring/alerts.yml` — правила: контейнер упал, диск > 80%, OOM
- [ ] Grafana дашборды (JSON provisioning):
  - **Контейнеры** — CPU/Memory/Network/Disk per container (cAdvisor)
  - **Хост** — CPU, RAM, disk I/O, network (Node Exporter)
  - **Приложение** — заготовка под метрики из `core/metrics.py` (Track D заполнит)

**Зависит от:** ничего (фундамент).
**Отдаёт:** пакет `core`, `core/metrics.py`, поднятый стек мониторинга. ⚠️ Узкое место — отдать контракты и стабы в первую очередь.

### 🅲 Track C — Agent + Autonomy (Dev C)

- [ ] `BaseAgent` / ReAct-цикл: LLM → tool_calls → выполнение → повтор → финальный ответ
- [ ] Интеграция с `ToolRegistry` (берёт доступные тулзы из конфига агента)
- [ ] **Autonomy Gate**: перед вызовом тула проверка risk → low=авто, medium+=confirm (interrupt)
- [ ] Персист: каждое действие → `actions`, шаги → `traces`, ожидание → `confirms`
- [ ] AgentSpec оркестратора (промпт + список тулзов) — пока в коде/конфиге, не в БД
- [ ] Возобновление после ответа на confirm

**Зависит от:** контракты LLM, ToolRegistry, DB (день 1). Кодит против них + моки тулзов.
**Отдаёт:** `invoke(message, session_id)` — рабочий агент.

### 🅳 Track D — Entry points + Observability (Dev D)

**Entry points:**
- [ ] HTTP в `platform-api`: `POST /chat`, `POST /confirm/{id}`, `GET /actions`, `GET /traces/{id}`
- [ ] Confirm-флоу: вернуть pending_confirm, принять ответ, возобновить агента
- [ ] Read-модель: листинг действий + просмотр трейса (для отладки и демо)
- [ ] **Telegram-адаптер (aiogram)** — чат + кнопки confirm (must для красивого демо)
- [ ] Простой лог/вывод трейса

**Мониторинг приложения (поверх инфраструктуры Track B):**
- [ ] `GET /metrics` endpoint в `platform-api` (prometheus-client, multiprocess mode)
- [ ] Grafana дашборды (provisioning из JSON):
  - **LLM** — запросы/сек, latency p50/p95/p99, токены (prompt/completion), ошибки по типу
  - **Внешние сервисы** — Yandex Tracker latency, статус-коды
  - **Агенты** — трейсы (completed/failed), actions по tool/risk, confirms pending/resolved
  - **Здоровье сервисов** — HTTP error rate, DB pool utilization, uptime
- [ ] Alertmanager правила приложения: error rate > 5%, LLM latency p95 > 10s, confirms зависли > 30 мин
- [ ] Telegram-алерты подключить к Alertmanager из Track B

**Зависит от:** `invoke()` агента (контракт), DB-модели. Кодит против стабов.
**Зависит от (мониторинг):** инфраструктура из Track B + `core/metrics.py`.
**Отдаёт:** способ поговорить с агентом + увидеть, что он сделал + application-level дашборды.

---

## Порядок и зависимости

```mermaid
gantt
    title Базовый функционал
    dateFormat YYYY-MM-DD
    axisFormat %d.%m

    section День 1 (вместе)
    Контракты + Tracker setup        :crit, d0, 2026-06-03, 1d

    section Track A — Tracker
    TrackerClient                    :a1, after d0, 2d
    tracker_* тулзы + smoke          :a2, after a1, 2d

    section Track B — Core + Monitoring
    config + db + миграции           :done, b1, after d0, 2d
    llm + tool registry + prompts    :done, b2, after b1, 2d
    core/metrics.py (prometheus)     :b3, after b2, 1d
    docker-compose.monitoring.yml    :b4, after b3, 1d
    cAdvisor + Node Exporter дашборды :b5, after b4, 1d

    section Track C — Agent
    ReAct loop (на моках)            :c1, after d0, 3d
    Autonomy Gate + персист          :c2, after c1, 2d

    section Track D — Entry
    HTTP /chat /confirm /actions     :dd1, after d0, 3d
    Telegram-адаптер                 :dd2, after dd1, 1d
    Application дашборды + алерты    :dd3, after dd2, 1d

    section Финал (вместе)
    Интеграция среза + демо          :crit, fin, after a2 b2 c2 dd2, 2d
```

**Критический путь:** контракты (день 1) → Track B отдаёт реальные интерфейсы → A/C/D заменяют моки на реальное → интеграция. Track B — приоритет, чтобы не стать бутылочным горлышком.

---

## Definition of Done (базовый функционал)

- [ ] Тестовая очередь в Трекере создана, токен в секретах
- [ ] `POST /chat {"message": "заведи задачу: починить логин, срочно"}` → агент создаёт issue
- [ ] Medium-risk действие ушло на confirm, после `POST /confirm/{id}` задача появилась в Трекере
- [ ] `GET /actions` показывает действие, `GET /traces/{id}` — шаги рассуждения
- [ ] Telegram: то же самое работает через бота с кнопками
- [ ] Всё деплоится на тест-VPS через push в `develop`
- [ ] Grafana открывается на тест-VPS: дашборды контейнеров и хоста работают (Track B)
- [ ] Grafana application-дашборды показывают LLM-метрики и метрики агентов (Track D)
- [ ] Telegram-алерт приходит если контейнер упал или диск > 80% (Track B) или error rate > 5% (Track D)

---

## Развилки перед стартом

1. **Организация Трекера:** Яндекс 360 (проще, OAuth) или Yandex Cloud Org (IAM, 12ч)? → рекомендую Яндекс 360.
2. **Мессенджер:** Telegram (быстрее всего, aiogram, кнопки) или Я.Мессенджер? → рекомендую Telegram для MVP.
3. **Кто владеет Яндекс-аккаунтом** (организация + токен) — назначить ответственного в день 1.
4. **Модель в Yandex AI Studio:** какая именно (YandexGPT Pro / Lite)? Проверить доступ и лимиты в день 1.

---

Sources:
- [Как выбрать тариф для Яндекс Трекера](https://yandex.ru/support/tracker/ru/pricing)
- [Yandex Tracker API — Creating an issue](https://yandex.ru/support/tracker/en/api-ref/issues/create-issue)
- [Yandex Tracker API — Creating a queue](https://yandex.ru/support/tracker/en/api-ref/queues/create-queue)
- [Yandex Tracker — API access (auth)](https://yandex.ru/support/tracker/en/concepts/access)
