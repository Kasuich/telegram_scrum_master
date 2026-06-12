# Документация PM Agent Platform

Карта документации. Начать с нуля — корневой **[README](../README.md)**
(установка и запуск). Здесь — справочники и дизайн-доки.

## Архитектура

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — карта системы, сервисы, потоки данных, рантайм агента, расписания, Telegram, eval.
- **[TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md)** — стратегический взгляд и roadmap.
- **[SERVICES.md](SERVICES.md)** — покомпонентный справочник по всем 6 сервисам и web-ui.
- **[CORE_LIBRARY.md](CORE_LIBRARY.md)** — модули `packages/core` (агент-фреймворк, ReAct, LLM, Трекер, eval…).
- **[DATA_MODEL.md](DATA_MODEL.md)** — схема PostgreSQL (38 таблиц) и история миграций.

## Эксплуатация и настройка

- **[CONFIGURATION.md](CONFIGURATION.md)** — все переменные окружения по группам.
- **[DEPLOYMENT.md](DEPLOYMENT.md)** — CI/CD, провижининг VPS, публичный контур (nginx/TLS/WireGuard).
- **[MONITORING.md](MONITORING.md)** — Prometheus/Grafana/Loki/Alertmanager и дашборды.
- **[runbooks/telegram-gateway-runbook.md](runbooks/telegram-gateway-runbook.md)** — runbook шлюза.

## Агенты и рантайм

- **[agents.md](agents.md)** — code-first агенты: `BaseAgent`/`BaseBot`/`EntryPoint`/`BotRegistry`.
- **[ADDING_AGENTS.md](ADDING_AGENTS.md)** — как добавить нового агента (один файл).
- **[pm_agent_stage_graph.md](pm_agent_stage_graph.md)** — детерминированный граф стадий `pm_agent` (со схемами).
- **[agent_evaluation.md](agent_evaluation.md)** — «Штурм»: оценка качества агента (LLM-as-a-judge).

## Подсистемы

- **[meeting_capture.md](meeting_capture.md)** — запись и расшифровка встреч Telemost.
- **[SCRUMIC_DESIGN.md](SCRUMIC_DESIGN.md)** — питомец «Скрамик» и «Битва скрамиков» (дизайн + ассеты).
- **[TELEGRAM_SETUP_GUIDE.md](TELEGRAM_SETUP_GUIDE.md)** — подключение Telegram и доступ к чатам.

## Интеграция с Трекером

- **[TRACKER_MCP_SETUP.md](TRACKER_MCP_SETUP.md)** — настройка Yandex Tracker MCP в Yandex Cloud.
- **[tracker_queue_DARKHORSE.md](tracker_queue_DARKHORSE.md)** — справочник полей очереди DARKHORSE.

## Ассеты

- `diagrams/` — SVG-схемы графа стадий (используются в `pm_agent_stage_graph.md`), генератор `generate_svgs.py`.
- `scrumiks/` — спрайты питомцев и косметика (PNG + рендер-скрипты).
- `agents/png/` — пиксельные логотипы агентов (используются веб-консолью и Mini App).
