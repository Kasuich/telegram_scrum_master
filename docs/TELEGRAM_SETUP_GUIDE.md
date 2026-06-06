# Гайд: подключение Telegram и доступ к чатам

Платформа работает с Telegram через два сервера: **основной** (Postgres, platform-api,
агенты) и **Telegram-сервер** (telegram-gateway, единственное место, где живёт
bot token). Этот документ проводит через весь процесс — от создания бота до чтения
сообщений через API.

---

## Содержание

1. [Создать бота в BotFather](#1-создать-бота-в-botfather)
2. [Подготовить Telegram-сервер](#2-подготовить-telegram-сервер)
3. [Настроить основной сервер](#3-настроить-основной-сервер)
4. [Запустить gateway и зарегистрировать webhook](#4-запустить-gateway-и-зарегистрировать-webhook)
5. [Создать Installation и привязать чаты](#5-создать-installation-и-привязать-чаты)
6. [Подключить группу или канал](#6-подключить-группу-или-канал)
7. [Подключить личные сообщения (DM)](#7-подключить-личные-сообщения-dm)
8. [Secretary Mode — читать личные чаты пользователя](#8-secretary-mode--читать-личные-чаты-пользователя)
9. [Читать сообщения через API](#9-читать-сообщения-через-api)
10. [Проверить, что всё работает](#10-проверить-что-всё-работает)
11. [Частые проблемы](#11-частые-проблемы)

---

## 1. Создать бота в BotFather

### 1.1 Создать нового бота

Откройте [@BotFather](https://t.me/BotFather) и выполните:

```
/newbot
```

BotFather спросит имя и username. После создания вы получите:

```
Done! Congratulations on your new bot.
Use this token to access the HTTP API:
1234567890:AABBCCDDEEFFaabbccddeeff1234567890
```

Сохраните этот токен — это `TELEGRAM_BOT_TOKEN`.

### 1.2 Отключить Privacy Mode (для групп)

По умолчанию бот получает только сообщения, адресованные ему (команды и упоминания).
Чтобы сохранять все сообщения группы:

```
/setprivacy
→ выбрать вашего бота
→ Disable
```

> **Важно.** Если бот уже добавлен в группу до отключения privacy mode, нужно
> удалить его и добавить заново — Telegram применяет настройку в момент добавления.

### 1.3 Для каналов — сделать бота администратором

В канале: Управление → Администраторы → добавить бота. Права: минимум «Read messages».

---

## 2. Подготовить Telegram-сервер

Telegram-сервер — это VPS с публичным IP, откуда доступен `api.telegram.org`.
Основной сервер **не должен** иметь доступа к Telegram.

### 2.1 Установить зависимости

```bash
# На Telegram-сервере
apt install docker.io docker-compose-plugin nginx certbot python3-certbot-nginx wireguard
```

### 2.2 Получить TLS-сертификат

Telegram требует HTTPS для webhook:

```bash
certbot --nginx -d your-gateway-domain.com
# сертификаты окажутся в /etc/letsencrypt/live/your-gateway-domain.com/
```

### 2.3 Сгенерировать HMAC-ключ

Это симметричный ключ для подписи запросов между gateway и основным сервером:

```bash
KEY_ID="key1"
SECRET=$(openssl rand -hex 32)
echo "$KEY_ID:$SECRET"
# Пример: key1:a3f8b2c1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1
```

Сохраните обе части — они нужны на обоих серверах.

### 2.4 Создать .env для gateway

Скопируйте шаблон и заполните:

```bash
cp config/telegram-gateway/.env.example /opt/telegram-gateway/.env
```

Отредактируйте `/opt/telegram-gateway/.env`:

```bash
# Обязательные
TELEGRAM_BOT_TOKEN=1234567890:AABBCCDDEEFFaabbccddeeff1234567890
TELEGRAM_WEBHOOK_SECRET=случайная-строка-минимум-32-символа
TELEGRAM_WEBHOOK_BASE_URL=https://your-gateway-domain.com
MAIN_BRIDGE_URL=https://10.0.0.1/internal/telegram/v1   # WireGuard-адрес основного сервера

# HMAC-подпись запросов к основному серверу
TELEGRAM_BRIDGE_HMAC_KEY_ID=key1
TELEGRAM_BRIDGE_HMAC_KEY=a3f8b2c1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1

# Опциональные (можно оставить дефолты)
GATEWAY_SPOOL_PATH=/var/lib/telegram-gateway/spool.db
TELEGRAM_OUTBOX_LEASE_SECONDS=60
GATEWAY_MAX_ATTEMPTS=8
LOG_LEVEL=INFO
ENVIRONMENT=production
```

> `TELEGRAM_WEBHOOK_SECRET` — произвольная строка, которую Telegram будет посылать
> в заголовке `X-Telegram-Bot-Api-Secret-Token` с каждым webhook-запросом.
> Генерируйте: `openssl rand -hex 32`

### 2.5 Настроить Nginx

Конфиг уже есть в `config/nginx/telegram-gateway.conf`. Скопируйте:

```bash
cp config/nginx/telegram-gateway.conf /etc/nginx/sites-available/telegram-gateway
# Замените gateway.example.com на ваш домен
sed -i 's/gateway.example.com/your-gateway-domain.com/g' /etc/nginx/sites-available/telegram-gateway

# Укажите путь к сертификатам (если certbot положил в другое место)
# Ожидаемые пути:
#   /etc/nginx/ssl/fullchain.pem
#   /etc/nginx/ssl/privkey.pem
ln -sf /etc/letsencrypt/live/your-gateway-domain.com/fullchain.pem /etc/nginx/ssl/fullchain.pem
ln -sf /etc/letsencrypt/live/your-gateway-domain.com/privkey.pem /etc/nginx/ssl/privkey.pem

ln -sf /etc/nginx/sites-available/telegram-gateway /etc/nginx/sites-enabled/
nginx -t && nginx -s reload
```

Nginx проксирует `/telegram/webhook` → `http://telegram-gateway:8080/webhook`.
Снаружи доступны только `/telegram/webhook`, `/health` и `/metrics`.

---

## 3. Настроить основной сервер

### 3.1 Переменные окружения platform-api

Добавьте в `.env` основного сервера:

```bash
# Включить Telegram bridge
TELEGRAM_BRIDGE_ENABLED=true

# Тот же ключ, что на gateway (формат: key_id:secret,key_id2:secret2)
TELEGRAM_BRIDGE_HMAC_KEYS=key1:a3f8b2c1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1

# Сколько секунд принимать nonce как свежий (default 300)
TELEGRAM_BRIDGE_NONCE_TTL=300

# Сколько секунд gateway удерживает lease на outbox-запись (default 60)
TELEGRAM_OUTBOX_LEASE_SECONDS=60
```

> `TELEGRAM_BOT_TOKEN` на основном сервере **отсутствует**. Если он там есть —
> удалите.

### 3.2 Применить миграции

Telegram-таблицы создаются существующей Alembic-миграцией:

```bash
cd services/platform-api
uv run alembic upgrade head
```

Созданные таблицы: `telegram_installations`, `telegram_chats`, `telegram_users`,
`telegram_user_links`, `telegram_business_connections`, `telegram_updates`,
`telegram_messages`, `telegram_outbox`, `telegram_callback_tokens`,
`telegram_import_jobs`, `telegram_notification_preferences`.

### 3.3 WireGuard (опционально, рекомендуется)

Если bridge слушает только на WireGuard-адресе, токен никогда не попадёт в
публичную сеть. Настройте WireGuard между серверами и укажите внутренний адрес
в `MAIN_BRIDGE_URL`.

---

## 4. Запустить gateway и зарегистрировать webhook

### 4.1 Запуск

На Telegram-сервере:

```bash
cd /opt/telegram-gateway
docker compose -f docker-compose.telegram-gateway.yml up -d
```

Проверьте, что сервис поднялся:

```bash
curl http://localhost:8080/health
# {"status": "ok"}

curl http://localhost:8080/health/ready
# {"status": "ready", "spool_ok": true, "bridge_ok": true}
```

### 4.2 Регистрация webhook

Gateway регистрирует webhook **автоматически при старте**. Проверить, что webhook
зарегистрирован:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getWebhookInfo"
```

Ожидаемый ответ:

```json
{
  "ok": true,
  "result": {
    "url": "https://your-gateway-domain.com/telegram/webhook",
    "has_custom_certificate": false,
    "pending_update_count": 0,
    "last_error_message": ""
  }
}
```

Если `url` пустой или не совпадает — перезапустите gateway:

```bash
docker compose -f docker-compose.telegram-gateway.yml restart telegram-gateway
```

---

## 5. Создать Installation и привязать чаты

**Installation** — это запись в БД, которая связывает конкретного Telegram-бота с
командой платформы. Без неё сообщения игнорируются.

### 5.1 Создать Installation вручную (SQL)

На основном сервере выполните в Postgres:

```sql
INSERT INTO telegram_installations (
    id,
    team_id,
    alias,
    external_bot_id,
    mode,
    status,
    settings,
    created_at,
    updated_at
) VALUES (
    gen_random_uuid(),
    '<ваш team_id>',                    -- UUID команды в платформе
    'my_workspace_bot',                  -- произвольный alias
    '1234567890',                        -- числовой ID бота (из getMe)
    'workspace_bot',
    'active',
    '{"bot_username": "my_bot"}',       -- username без @
    now(),
    now()
)
RETURNING id;
```

Запишите возвращённый `id` — это `installation_id`.

> Получить числовой ID бота: `curl https://api.telegram.org/bot$TOKEN/getMe`
> → поле `result.id`

---

## 6. Подключить группу или канал

Чат появляется в системе после первого сообщения в него **при активной привязке**.

### 6.1 Добавить бота в группу

1. Добавьте бота в группу или супергруппу.
2. Для канала — сделайте бота администратором.
3. Напишите любое сообщение (или /start для личного чата).

Gateway получит update от Telegram, передаст его на основной сервер. Основной
сервер автоматически создаёт запись в `telegram_chats` с `ingest_mode = 'disabled'`.

### 6.2 Включить сбор сообщений

По умолчанию новый чат имеет `ingest_mode = 'disabled'` — сообщения не
сохраняются. Измените режим:

```sql
UPDATE telegram_chats
SET ingest_mode = 'mentions',    -- или 'correspondence', 'direct', 'archive_only'
    active = true
WHERE installation_id = '<installation_id>'
  AND external_chat_id = '-1001234567890';  -- ID чата (отрицательный для групп)
```

| `ingest_mode` | Сохранение | Вызов агента |
|---|---|---|
| `disabled` | нет | нет |
| `mentions` | все сообщения | команды, упоминания, reply боту |
| `correspondence` | все сообщения | правила Correspondence Agent (пока только хранит) |
| `direct` | все сообщения | каждое DM |
| `archive_only` | все сообщения | никогда |

Как узнать `external_chat_id` группы: Telegram присылает его в каждом update как
`message.chat.id`. Найдите в `telegram_updates` после первого сообщения:

```sql
SELECT payload->'message'->'chat'->>'id' AS chat_id,
       payload->'message'->'chat'->>'title' AS title
FROM telegram_updates
WHERE installation_id = '<installation_id>'
ORDER BY received_at DESC
LIMIT 10;
```

### 6.3 Проверить привязку

```sql
SELECT external_chat_id, type, title, ingest_mode, active
FROM telegram_chats
WHERE installation_id = '<installation_id>';
```

---

## 7. Подключить личные сообщения (DM)

Бот не может первым написать пользователю. Пользователь должен сам открыть диалог.

### 7.1 Сгенерировать deep link

Создайте одноразовый onboarding-токен для пользователя:

```sql
INSERT INTO telegram_callback_tokens (
    id,
    token,
    token_type,
    installation_id,
    team_id,
    allowed_user_id,    -- NULL = любой пользователь, иначе UUID пользователя платформы
    payload,
    expires_at,
    created_at
) VALUES (
    gen_random_uuid(),
    encode(gen_random_bytes(16), 'hex'),  -- opaque token
    'onboarding',
    '<installation_id>',
    '<team_id>',
    '<user_id_or_null>',
    '{"action": "link_dm"}',
    now() + interval '7 days',
    now()
)
RETURNING token;
```

Отправьте пользователю ссылку:

```
https://t.me/your_bot?start=<token>
```

Когда пользователь нажмёт Start, gateway передаст `/start <token>` на основной
сервер. Сервер свяжет Telegram-пользователя с командой и DM-чат начнёт работать.

### 7.2 Активировать режим direct для DM

После того как пользователь нажал Start, его чат появится в `telegram_chats`:

```sql
UPDATE telegram_chats
SET ingest_mode = 'direct'
WHERE installation_id = '<installation_id>'
  AND type = 'private'
  AND external_chat_id = '<telegram_user_id>';
```

---

## 8. Secretary Mode — читать личные чаты пользователя

Secretary Mode (Bot API 10.0) позволяет пользователю подключить бота к своему
аккаунту и выбрать, какие личные чаты видит бот. Требует **отдельного согласия**
каждого пользователя.

> Secretary Mode не заменяет workspace bot. Это отдельный connection с отдельной
> политикой доступа.

### 8.1 Включить Secretary Mode в BotFather

```
/mybots → выбрать бота → Bot Settings → Business Mode → Enable
```

### 8.2 Как пользователь подключает бота

1. Пользователь открывает Telegram → Настройки → Telegram for Business → Connected Bots.
2. Выбирает вашего бота.
3. Разрешает доступ к выбранным чатам.

После этого gateway получит `business_connection` update и передаст его на
основной сервер. Сервер создаст запись в `telegram_business_connections`.

### 8.3 Проверить подключение

```sql
SELECT
    business_connection_id,
    can_reply,
    selected_chat_policy,
    status,
    connected_at
FROM telegram_business_connections
WHERE team_id = '<team_id>'
ORDER BY connected_at DESC;
```

Поле `selected_chat_policy`:
- `{}` — доступны все разрешённые чаты
- `{"chat_ids": ["-100123", "-100456"]}` — только указанные чаты

### 8.4 Ответ от имени пользователя

Ответы через Secretary Mode требуют явного подтверждения (confirm flow). Агент
создаёт `proposal`, gateway доставляет только при `can_reply=true` и активном
connection. При revoke ожидающие deliveries отменяются автоматически.

---

## 9. Читать сообщения через API

Все сохранённые сообщения доступны через внутренний API.

### 9.1 Endpoint

```
GET /internal/telegram/v1/messages
```

### 9.2 Параметры запроса

| Параметр | Тип | Обязательный | Описание |
|---|---|---|---|
| `team_id` | UUID | **да** | ID команды |
| `installation_id` | UUID | нет | Фильтр по боту |
| `chat_id` | UUID | нет | Фильтр по чату (internal UUID, не external_chat_id) |
| `direction` | string | нет | `inbound` или `outbound` |
| `access_mode` | string | нет | `workspace_bot`, `secretary`, `import` |
| `sent_after` | datetime | нет | ISO 8601, фильтр по дате |
| `sent_before` | datetime | нет | ISO 8601, фильтр по дате |
| `include_deleted` | bool | нет | Включить удалённые сообщения |
| `limit` | int | нет | Размер страницы, default 50, max 200 |
| `cursor_sent_at` | datetime | нет | Cursor pagination — дата |
| `cursor_id` | UUID | нет | Cursor pagination — ID |

### 9.3 Пример запроса

```bash
# Переменные
TIMESTAMP=$(date +%s)
NONCE=$(uuidgen | tr -d '-')
METHOD="GET"
PATH="/internal/telegram/v1/messages"
BODY=""
BODY_SHA=$(echo -n "$BODY" | sha256sum | awk '{print $1}')
SIGNED="${METHOD}\n${PATH}\n${TIMESTAMP}\n${NONCE}\n${BODY_SHA}"
SIG=$(printf "$SIGNED" | openssl dgst -sha256 -hmac "$HMAC_SECRET" | awk '{print $2}')

curl -s "https://main-server/internal/telegram/v1/messages?team_id=<uuid>&limit=50" \
  -H "X-Bridge-Timestamp: $TIMESTAMP" \
  -H "X-Bridge-Nonce: $NONCE" \
  -H "X-Bridge-Signature: $SIG" \
  -H "X-Bridge-Key-Id: key1"
```

### 9.4 Ответ

```json
{
  "messages": [
    {
      "id": "uuid",
      "external_message_id": "42",
      "external_chat_id": "-1001234567890",
      "direction": "inbound",
      "access_mode": "workspace_bot",
      "text": "Привет!",
      "sender_external_id": "99991111",
      "sender_name": "Иван",
      "sent_at": "2026-06-06T10:00:00Z",
      "reply_to_external_id": null,
      "message_thread_id": null,
      "message_kind": "text",
      "is_edited": false,
      "is_deleted": false
    }
  ],
  "next_cursor_sent_at": "2026-06-06T10:00:00Z",
  "next_cursor_id": "uuid",
  "has_more": true
}
```

### 9.5 Cursor pagination

Для постраничного обхода используйте `next_cursor_sent_at` и `next_cursor_id`
из предыдущего ответа:

```bash
curl "...?team_id=<uuid>&limit=50&cursor_sent_at=2026-06-06T10:00:00Z&cursor_id=<uuid>"
```

---

## 10. Проверить, что всё работает

### Чеклист после настройки

**Gateway:**

```bash
# 1. Сервис поднят
curl https://your-gateway-domain.com/health

# 2. Webhook зарегистрирован
curl "https://api.telegram.org/bot$TOKEN/getWebhookInfo" | python3 -m json.tool

# 3. Heartbeat доходит до основного сервера (в логах platform-api)
docker compose -f docker-compose.telegram-gateway.yml logs -f | grep heartbeat
```

**Основной сервер:**

```bash
# 4. Метрики ingest
curl https://main-server/metrics | grep telegram_bridge_ingest_total

# 5. Есть ли telegram_chats после первого сообщения
psql $DATABASE_URL -c "SELECT external_chat_id, ingest_mode FROM telegram_chats LIMIT 10;"
```

**Тест сквозной маршрутизации:**

1. Напишите боту в Telegram: `@bot_username привет`
2. Проверьте, что сообщение попало в БД:

```sql
SELECT text, direction, sent_at
FROM telegram_messages
ORDER BY sent_at DESC
LIMIT 5;
```

3. Если `ingest_mode = 'mentions'` — бот должен ответить в тот же чат/тему.

---

## 11. Частые проблемы

### Сообщения не попадают в `telegram_messages`

1. Проверьте `ingest_mode` чата — он должен быть не `disabled`:
   ```sql
   SELECT ingest_mode FROM telegram_chats WHERE external_chat_id = '<id>';
   ```
2. Проверьте, что gateway передаёт updates:
   ```bash
   docker compose logs telegram-gateway | grep "ingest"
   ```
3. Проверьте `telegram_updates` — raw updates должны там быть:
   ```sql
   SELECT status, error FROM telegram_updates ORDER BY received_at DESC LIMIT 10;
   ```

### Gateway не может достучаться до основного сервера

```
# На Telegram-сервере
curl -v $MAIN_BRIDGE_URL/health
```

Проверьте WireGuard (`wg show`), firewall на основном сервере.

### HMAC 401 Unauthorized

- Убедитесь, что `TELEGRAM_BRIDGE_HMAC_KEY` на gateway совпадает с
  `TELEGRAM_BRIDGE_HMAC_KEYS` на основном сервере (формат: `key_id:secret`)
- Проверьте синхронизацию времени: `timedatectl status` на обоих серверах
- Nonce TTL по умолчанию 300 секунд — расхождение часов > 5 минут вызовет ошибку

### Bot token на основном сервере

```bash
grep -r TELEGRAM_BOT_TOKEN /etc /opt --include="*.env" --include="*.yml" 2>/dev/null
```

Если что-то нашлось — удалите. Токен должен быть **только** на Telegram-сервере.

### Группа не получает все сообщения

Privacy mode включён. В BotFather:
```
/setprivacy → <ваш бот> → Disable
```
Затем удалите бота из группы и добавьте снова.

### Dead-letter items в outbox

```sql
SELECT status, count(*) FROM telegram_outbox GROUP BY status;
```

Если есть `dead_letter` — найдите причину в `last_error`:
```sql
SELECT last_error, count(*) FROM telegram_outbox WHERE status = 'dead_letter' GROUP BY last_error;
```

После устранения причины — replay через admin endpoint:
```bash
# POST /internal/telegram/v1/outbox:replay-dead-letter
# (с HMAC-заголовками, как в примере в разделе 9.3)
curl -X POST https://main-server/internal/telegram/v1/outbox:replay-dead-letter \
  -H "Content-Type: application/json" \
  ... # HMAC headers
  -d '{"limit": 50}'
```

---

## Итог: минимальная последовательность для первого запуска

```
BotFather: /newbot + /setprivacy Disable
↓
openssl rand -hex 32 → HMAC_KEY
↓
Telegram-сервер: заполнить .env, docker compose up
↓
Основной сервер: добавить env vars, alembic upgrade head
↓
SQL: INSERT INTO telegram_installations (...)
↓
Добавить бота в группу / написать /start
↓
SQL: UPDATE telegram_chats SET ingest_mode = 'mentions'
↓
Написать @bot_username сообщение → увидеть в telegram_messages
```
