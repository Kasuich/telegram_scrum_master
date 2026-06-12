# Деплой и инфраструктура

> CI/CD, провижининг VPS, публичный Telegram-контур (nginx/TLS/WireGuard) и
> деплой шлюза. Локальный запуск — [README](../README.md). Топология контура —
> [ARCHITECTURE → Telegram](ARCHITECTURE.md#7-telegram-контур).

---

## 1. CI/CD (GitHub Actions)

Три workflow в `.github/workflows/`:

### `ci.yml` — линт и тесты
- **Триггеры:** PR в `develop`/`main`, push в `develop`.
- **Шаги:** `uv` → Python 3.12 → `uv sync --all-packages` →
  `uv run pytest packages/core/tests/unit services/platform-api/tests services/pm-orchestrator/tests`.
- Секреты не нужны.

### `deploy-test.yml` — авто-деплой на тест-VPS
- **Триггер:** push в `develop` (continuous deployment).
- **Шаги:** rsync кода на VPS → запись `.env.test` из секретов → очистка старых
  контейнеров → `docker compose -f docker-compose.yml -f docker-compose.test.yml
  -f docker-compose.monitoring.yml up --build`.
- **Секреты:** `VPS_HOST`/`VPS_USER`/`VPS_SSH_KEY`, `TEST_DB_PASSWORD`,
  `YC_API_KEY`/`YC_FOLDER_ID`, `OPENROUTER_API_KEY`,
  `TRACKER_TOKEN`/`TRACKER_ORG_ID`/`TRACKER_MCP_TOKEN`,
  `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`, `GRAFANA_USER`/`GRAFANA_PASSWORD`,
  `TELEGRAM_BRIDGE_ENABLED`/`TELEGRAM_BRIDGE_HMAC_KEYS`,
  `S3_*`, `SPEECHKIT_API_KEY`.

### `deploy-telegram-gateway.yml` — деплой шлюза (ручной)
- **Триггер:** `workflow_dispatch` с входами `environment`
  (`telegram-staging`/`telegram-production`), `action` (`deploy`/`rollback`),
  `transport_mode` (`webhook`/`polling`).
- **Build:** сборка и push образа в `ghcr.io/<owner>/telegram-gateway:<env>-<sha>`.
- **Deploy:** SSH на `TG_VPS_HOST` → загрузка образа и compose → (опц.) настройка
  WireGuard-пира → запись `.env` → `docker compose up -d` → health-check
  `/health/live` → провижининг nginx+TLS через `setup-tunnel-nginx.sh` →
  проверка `getWebhookInfo`.
- **Rollback:** откат на предыдущий образ.
- **Секреты:** `TG_VPS_*`, `TELEGRAM_BOT_TOKEN_DIRECT`, `TELEGRAM_WEBHOOK_SECRET`,
  `TELEGRAM_WEBHOOK_BASE_URL`, `MAIN_BRIDGE_URL`, `TELEGRAM_BRIDGE_HMAC_KEY_*`,
  `TG_DOMAIN`, `CERTBOT_EMAIL`, `MAIN_WG_IP`, `WG_MAIN_SERVER_*`.

### Полезные команды

```bash
gh run list --limit 5        # статус деплоев
gh pr list                   # открытые PR
docker logs test-pm-orchestrator-1 -f
docker logs test-platform-api-1 -f
```

---

## 2. Провижининг VPS

`scripts/vps-setup.sh` — бутстрап чистого Ubuntu 22.04/24.04:

```bash
bash scripts/vps-setup.sh <github_deploy_public_key> [wg_peer_public_key] [wg_endpoint]
```

Ставит Docker + compose-плагин, заводит пользователя `deploy`, добавляет ключ
GitHub Actions, создаёт `/opt/pm-agent/test`, клонирует репозиторий на `develop`,
(опц.) поднимает WireGuard-туннель и печатает публичный ключ для пира.

---

## 3. Публичный Telegram-контур

Шлюз живёт на отдельном сервере с публичным IP и TLS (`misisdarkhorse.ru`),
основной стек — в приватной сети, связь — через WireGuard.

```
Telegram API
   │ webhook (HTTPS)
   ▼
nginx + TLS (Let's Encrypt)          ← setup-tunnel-nginx.sh
   ├─ /telegram/webhook → 127.0.0.1:8080  (telegram-gateway)
   ├─ /grafana/         → 10.99.0.1:3000  (через WireGuard)
   └─ /                 → 10.99.0.1:5173  (web-ui через WireGuard)

telegram-gateway ⇄ platform-api /internal/telegram/v1  (HMAC nonce, через WireGuard)
```

### `scripts/setup-tunnel-nginx.sh` (идемпотентный)
Ставит nginx+certbot, поднимает HTTP для ACME-челленджа, выпускает/обновляет
сертификат Let's Encrypt, пишет HTTPS-конфиг с реверс-прокси и перезагружает nginx.
Требует `DOMAIN`, `CERTBOT_EMAIL`; опц. `MAIN_WG_IP`, `GATEWAY_ADDR`,
`GRAFANA_PORT`, `WEBUI_PORT`.

### Конфиги
- `config/telegram-gateway/.env.example` — env шлюза (webhook secret, bridge URL,
  HMAC, spool/ретраи/лимиты).
- `config/nginx/telegram-gateway.conf` — standalone nginx-конфиг шлюза.
- `docker-compose.telegram-gateway.yml` — контейнер шлюза (порт `127.0.0.1:8080`,
  персистентный spool `/var/lib/telegram-gateway`).

Эксплуатация и траблшутинг — [runbook](runbooks/telegram-gateway-runbook.md),
пошаговая настройка Telegram — [TELEGRAM_SETUP_GUIDE](TELEGRAM_SETUP_GUIDE.md).

---

## 4. Compose-файлы

| Файл | Назначение |
|------|------------|
| `docker-compose.yml` | Основной стек: postgres, pm-orchestrator, platform-api, console-api, meeting-capture, eval-runner, web-ui |
| `docker-compose.test.yml` | Оверрайд для теста (порты, `LOG_LEVEL=DEBUG`, изолированный том) |
| `docker-compose.monitoring.yml` | Observability: prometheus, alertmanager, grafana, loki, promtail, cadvisor, node-exporter |
| `docker-compose.telegram-gateway.yml` | Шлюз (отдельный сервер) |

Полезное:

```bash
# полный стек + мониторинг
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  -f docker-compose.monitoring.yml --env-file .env.test up --build -d

docker compose ps
docker compose logs -f pm-orchestrator
docker compose down            # остановить (тома сохраняются)
```

---

**См. также:** [MONITORING](MONITORING.md) · [CONFIGURATION](CONFIGURATION.md) ·
[ARCHITECTURE](ARCHITECTURE.md)
