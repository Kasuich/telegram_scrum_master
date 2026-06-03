# Дорожная карта до базового функционала

> Аудит на 2026-06-03. Цель: **вертикальный срез + Telegram**.
> Приоритет наполнения — Track C (ядро агента).

---

## Статус треков (аудит)

| Трек | Готовность | Что есть | Что блокирует срез |
|------|:---:|----------|--------------------|
| 🅱️ B — Core + Monitoring | ~95% | core (config/db/llm/tools), 11 таблиц, metrics, весь стек мониторинга, 3 дашборда | — (только проверить YandexGPT на боевом ключе) |
| 🅲 C — Agent + Autonomy | ~50% | BaseAgent, Autonomy Gate (конфиг), схемы actions/traces/confirms | ReAct-цикл, персист, invoke(session_id), confirm-resume |
| 🅰️ A — Tracker | ~10% | TrackerConfig, моки тулзов в examples | TrackerClient, реальные tracker_* тулзы |
| 🅳 D — Entry + Observability | ~40% | app-дашборд, 3 алерта, Alertmanager→TG | /chat, /confirm, /actions, /traces, /metrics, Telegram-бот |

---

## Дальнейшие шаги (в порядке приоритета)

### Шаг 0 — разблокировка (Track B) ✅ ГОТОВО
- [x] YandexGPT работает на боевом ключе (foundationModels v1 API, модель `yandexgpt`)
- [x] Smoke-тесты: basic completion, tool calling, yandexgpt-lite — 3/3

### Шаг 1 — ядро агента (Track C) ✅ ГОТОВО
- [x] **ReAct-цикл** (`core/react.py`): LLM → tool → result → повтор → финальный ответ, лимит 8 итераций
- [x] **`invoke(message, session_id) → AgentResult`**: reply или pending_confirm
- [x] **Autonomy Gate**: low=авто, medium/high=confirm, `always_confirm_tools` override
- [x] **Персист**: session state в памяти (in-memory), при наличии DB-сессии → Trace/Action/Confirm
- [x] **`resume(confirm_id, approved) → AgentResult`**: продолжение после ответа пользователя
- [x] AgentSpec оркестратора в коде (BaseAgent subclass)
- [x] 14 unit-тестов: авто-low, confirm-medium, resume approve/reject, tool chain, max_iterations

### Шаг 2 — реальный Трекер (Track A)
- [ ] `TrackerClient` (httpx, REST v3): create/get/update/transition/search + обработка 401/404/read-only.
- [ ] Тулзы `@platform_tool`: `tracker_create_issue` (medium), `tracker_update_issue`/`move`/`comment` (low), `tracker_get_issue`/`search` (read).
- [ ] Smoke-скрипт: создать/прочитать/закрыть задачу в реальной очереди `TEST`.
- [ ] Заменить моки в агенте на реальные тулзы.

### Шаг 3 — entry points (Track D)
- [ ] `POST /chat {message, session_id} → {reply, pending_confirm?}`
- [ ] `POST /confirm/{id} {approved} → {reply}`
- [ ] `GET /actions`, `GET /traces/{id}` (read-модель для отладки/демо)
- [ ] `GET /metrics` (prometheus-client) в platform-api → разблокирует app-дашборды.
- [ ] **Telegram-адаптер (aiogram)**: чат + inline-кнопки confirm, тот же `invoke/resume`.

### Шаг 4 — интеграция и демо (вместе)
- [ ] Сквозной прогон сценария на тест-VPS, проверка дашбордов и алертов.

---

## Definition of Done — «срез + Telegram»

- [ ] Тестовая очередь в Трекере создана, `TRACKER_TOKEN`/`TRACKER_ORG_ID` в секретах.
- [ ] `POST /chat {"message": "заведи задачу: починить логин, срочно"}` → агент рассуждает и вызывает `tracker_create_issue`.
- [ ] Medium-risk ушёл на confirm; после `POST /confirm/{id} {approved:true}` задача **реально появляется в Трекере**.
- [ ] `GET /actions` показывает действие, `GET /traces/{id}` — шаги рассуждения.
- [ ] Тот же флоу работает в **Telegram** через бота с inline-кнопками confirm.
- [ ] Всё деплоится на тест-VPS через push в `develop`.
- [ ] `GET /metrics` отдаёт метрики; Grafana app-дашборд показывает LLM- и agent-метрики.

**Отложено за рамки среза:** networked A2A, Meeting Capture, Correspondence/Analytics агенты, киллер-фича, полноценный UI, расширенные алерты (external/tool/pool).
