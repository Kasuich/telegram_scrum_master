# Инструкция по настройке Yandex Tracker MCP в Yandex Cloud

В Yandex Cloud доступен готовый шаблон Serverless Container для Яндекс Трекера, предоставляющий MCP-интерфейс.

Поскольку MCP-протокол использует Streamable HTTP транспорт (SSE), токен для авторизации в самом Трекере должен быть доступен контейнеру (серверу MCP) напрямую, так как MCP-клиент передает только заголовки для авторизации в *Gateway/Container*, но не в сам Трекер.

Для безопасной передачи токенов используются секреты Yandex Lockbox.

> **⚠️ Важно (проверено на практике).** Образ `aikts/yandex-tracker-mcp` читает токен Трекера из запроса **только** через заголовок `Authorization: Bearer <token>` (имя заголовка захардкожено). Но ingress Serverless Container в Yandex Cloud **перехватывает заголовок `Authorization`** и пытается провалидировать его как IAM-токен → `403 Forbidden: Not authorized`. Поэтому **передать токен Трекера per-request через прямой URL контейнера невозможно** — токен **обязан** быть зашит в переменные окружения контейнера (через Lockbox). Без этого любой вызов инструмента падает с `Auth token not found in request or environment` (при этом `tools/list` работает, т.к. список статичен).

## Шаг 1: Создание секрета в Yandex Lockbox
1. Перейдите в сервис **Yandex Lockbox** в консоли Yandex Cloud.
2. Создайте новый секрет, например `tracker-mcp-secrets`.
3. Добавьте следующие ключи:
   - Ключ: `TRACKER_TOKEN` — Значение: OAuth-токен для доступа к Трекеру (начинается с `y0_...`). Альтернативно можно использовать `TRACKER_IAM_TOKEN` или ключи сервисного аккаунта (`TRACKER_SA_KEY_ID`, `TRACKER_SA_SERVICE_ACCOUNT_ID`, `TRACKER_SA_PRIVATE_KEY`).
   - Ключ организации — **для облачной организации Yandex Cloud используйте `TRACKER_CLOUD_ORG_ID`** (наш случай: `TRACKER_ORG_TYPE=cloud`). `TRACKER_ORG_ID` — только для организаций Yandex 360. Значение нашей орг.: `bpfg59cip6b9gepvrqk9`.

## Шаг 2: Создание сервисного аккаунта
Контейнеру нужен сервисный аккаунт для чтения секрета из Lockbox.
1. Перейдите в **Service Accounts**.
2. Создайте новый аккаунт, например `tracker-mcp-sa`.
3. Назначьте ему роль `lockbox.payloadViewer` (и `kms.keys.encrypterDecrypter`, если секрет зашифрован ключом KMS).

## Шаг 3: Развертывание Serverless Container
Вы можете развернуть контейнер через консоль, CLI или Terraform.

### Через консоль Yandex Cloud:
1. Перейдите в **Serverless Containers**.
2. Создайте новый контейнер, выберите шаблон **Yandex Tracker MCP** (или укажите соответствующий Docker-образ, например, `ghcr.io/aikts/yandex-tracker-mcp:latest`).
3. В разделе **Параметры контейнера**:
   - Укажите сервисный аккаунт `tracker-mcp-sa`.
   - В разделе **Переменные окружения** задайте:
     - `TRANSPORT` = `streamable-http`
     - `HOST` = `0.0.0.0`
     - `PORT` = `8000` (или порт, который слушает ваш образ)
   - В разделе **Секреты** добавьте переменные, сославшись на секрет Lockbox:
     - Переменная: `TRACKER_TOKEN` -> Секрет: `tracker-mcp-secrets` -> Версия: текущая -> Ключ: `TRACKER_TOKEN`
     - Переменная: `TRACKER_ORG_ID` -> Секрет: `tracker-mcp-secrets` -> Версия: текущая -> Ключ: `TRACKER_ORG_ID`
4. Сохраните и разверните новую ревизию.

### Альтернатива: через Yandex Cloud CLI
```bash
yc serverless container revision deploy \
  --container-name tracker-mcp-server \
  --image ghcr.io/aikts/yandex-tracker-mcp:latest \
  --service-account-id <ID_ВАШЕГО_SA> \
  --secret "TRACKER_TOKEN=$SECRET_ID/$VERSION_ID/TRACKER_TOKEN" \
  --secret "TRACKER_CLOUD_ORG_ID=$SECRET_ID/$VERSION_ID/TRACKER_CLOUD_ORG_ID" \
  --environment TRANSPORT=streamable-http,HOST=0.0.0.0,PORT=8000
```

> Минимально для нашего контейнера `bba11klgcd484rnls5kc` достаточно прокинуть `TRACKER_TOKEN` и `TRACKER_CLOUD_ORG_ID` в окружение новой ревизии (через Lockbox-секреты или напрямую через `--environment`, если секрет не требуется). После деплоя клиент обращается к публичному `…/mcp` **без** заголовка `Authorization`.

## Шаг 4: Настройка сетевого доступа (Gateway)
Для публичного вызова к контейнеру без IAM-токенов (если вы хотите использовать свой статичный токен API) рекомендуется использовать **API Gateway**:
1. Создайте API Gateway.
2. Настройте интеграцию с Serverless Container.
3. Настройте авторизацию (API key, JWT или кастомную).
4. Если API Gateway не используется, вызов Serverless Container напрямую потребует передачи `Authorization: Bearer <IAM_TOKEN>` (или публичного доступа к контейнеру — *не рекомендуется*).

## Шаг 5: Настройка клиента (вашего приложения)
В файле `.env` вашего приложения (например, PM Agent) укажите URL развернутого контейнера или API Gateway:

```env
TRACKER_MCP_URL=https://bba11klgcd484rnls5kc.containers.yandexcloud.net/mcp
# Для ПУБЛИЧНОГО контейнера оставьте пустым: ingress сам перехватывает Authorization,
# а токен Трекера зашит в окружении контейнера. Заполняйте ТОЛЬКО если перед
# контейнером стоит API Gateway со своей авторизацией (тогда здесь ключ/IAM Gateway).
TRACKER_MCP_TOKEN=
TRACKER_MCP_TIMEOUT=60
```

Клиент в `packages/core/src/core/tracker_mcp.py` выполняет корректный Streamable HTTP handshake (`initialize` → `notifications/initialized` → вызов) и при пустом `TRACKER_MCP_TOKEN` не отправляет заголовок `Authorization`. Проверено: `tools/list` возвращает 19 инструментов через публичный контейнер.

## Важные нюансы
1. **Передача Session ID:** MCP-клиенты, работающие поверх Streamable HTTP (SSE), **обязаны** передавать заголовок (обычно `Mcp-Session-Id`), который возвращается при инициализации SSE-соединения.
2. **Обновление секретов:** При изменении значений в Lockbox необходимо создать **новую ревизию** Serverless Container, чтобы он подтянул обновленные секреты.
3. **IAM-авторизация:** Если вы обращаетесь к контейнеру напрямую без API Gateway, то `TRACKER_MCP_TOKEN` в вашем `.env` должен быть актуальным IAM-токеном Yandex Cloud.
