# PM Agent Platform

Мульти-агентная платформа для автоматизации работы Project Manager.
Агенты ведут Яндекс Трекер, делают саммари встреч, анализируют переписку и присылают алерты.

→ [Полное описание платформы и архитектура](docs/README.md)

---

## Быстрый старт для разработчика

### Что нужно

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — менеджер зависимостей Python
- Docker + Docker Compose
- Доступ к Yandex AI Studio (API key + folder ID)

### 1. Клонировать и установить зависимости

```bash
git clone https://github.com/Artem216/digital_breakthrough_2026.git
cd digital_breakthrough_2026
git checkout develop

uv sync --all-packages
```

### 2. Настроить окружение

```bash
cp .env.example .env.test
```

Открыть `.env.test` и заполнить:

```env
DB_USER=pm_agent_test
DB_NAME=pm_agent_test
DB_PASSWORD=любой_пароль

YC_API_KEY=<ключ из Yandex AI Studio>
YC_FOLDER_ID=<ID фолдера в Яндекс Облаке>

ENVIRONMENT=test
LOG_LEVEL=DEBUG
```

**Где взять YC_API_KEY:**
1. Открыть [Yandex AI Studio](https://console.yandex.cloud/) → API keys
2. Создать ключ, скопировать

**Где взять YC_FOLDER_ID:**
1. Консоль Яндекс Облака → выбрать фолдер → в URL: `folders/b1g...` — это и есть ID

### 3. Запустить локально

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml --env-file .env.test up --build
```

Сервисы поднимутся на:

| Сервис | URL |
|---|---|
| Platform API | http://localhost:8100 |
| Platform API docs | http://localhost:8100/docs |
| PostgreSQL | localhost:5433 |

Проверить что всё работает:
```bash
curl http://localhost:8100/health
# {"status": "ok"}
```

### 4. Остановить

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml down
```

Удалить данные БД тоже:
```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml down -v
```

---

## Как выкатить изменения на тест-сервер

### Флоу

```
1. Сделать ветку от develop
   git checkout develop && git pull
   git checkout -b feature/my-feature

2. Разработать, закоммитить

3. Запушить и открыть PR в develop
   git push -u origin feature/my-feature
   gh pr create --base develop

4. CI проверит: lint + тесты (автоматически)

5. Смержить PR в develop

6. GitHub Actions автоматически задеплоит на тест-сервер
   → через ~2 минуты изменения доступны по адресу:
   http://158.160.200.44:8100
```

### CI/CD пайплайн

```
PR открыт / push в develop
        │
        ▼
┌─────────────────┐
│  CI (ci.yml)    │
│  ─────────────  │
│  uv sync        │
│  ruff check     │  ← линтер
│  ruff format    │  ← форматирование
│  pytest         │  ← тесты
└────────┬────────┘
         │ только если зелёный
         ▼
┌────────────────────────────┐
│  Deploy → Test             │
│  (deploy-test.yml)         │
│  ──────────────────────    │
│  rsync кода на VPS         │  ← GitHub Actions → сервер
│  записать .env из Secrets  │  ← секреты НЕ хранятся на сервере
│  docker compose up --build │  ← пересобирает только изменившееся
│  docker compose ps         │  ← проверка что поднялось
└────────────────────────────┘
```

### Просмотр статуса деплоя

```bash
# Последние runs
gh run list --repo Artem216/digital_breakthrough_2026 --limit 5

# Следить за текущим run в реальном времени
gh run watch --repo Artem216/digital_breakthrough_2026
```

### Посмотреть логи на сервере

```bash
ssh -i ~/.ssh/yc_vps kasuich@158.160.200.44

# Состояние контейнеров
sudo docker ps

# Логи конкретного сервиса
sudo docker logs test-platform-api-1 -f
sudo docker logs test-pm-orchestrator-1 -f

# Все логи
sudo docker compose -f /opt/pm-agent/test/docker-compose.yml \
  -f /opt/pm-agent/test/docker-compose.test.yml logs -f
```

---

## Структура проекта

```
digital_breakthrough_2026/
│
├── packages/
│   └── core/                    # Общая библиотека (модели БД, клиенты, утилиты)
│       └── src/core/
│
├── services/
│   ├── platform-api/            # FastAPI сервер — REST + A2A endpoint
│   │   ├── src/platform_api/
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   │
│   └── pm-orchestrator/         # PM Orchestrator агент (LangGraph)
│       ├── src/pm_orchestrator/
│       ├── Dockerfile
│       └── pyproject.toml
│
├── tests/                       # Тесты (pytest)
│
├── docs/
│   └── README.md                # Подробная архитектура и описание платформы
│
├── .github/
│   └── workflows/
│       ├── ci.yml               # Линтер + тесты на каждый PR
│       └── deploy-test.yml      # Деплой на тест-сервер при push в develop
│
├── docker-compose.yml           # Базовая конфигурация (prod)
├── docker-compose.test.yml      # Тест-оверрайды (порты 8100, debug)
├── pyproject.toml               # uv workspace, ruff, pytest
├── uv.lock                      # Зафиксированные зависимости
└── .env.example                 # Шаблон для .env.test
```

---

## Ветки

| Ветка | Назначение |
|---|---|
| `main` | Продакшн (пока пустой) |
| `develop` | Тестовая среда — всё, что автоматически деплоится на сервер |
| `feature/*` | Фича-ветки → PR → develop |

---

## GitHub Secrets (для CI/CD)

Настроены в репозитории → Settings → Secrets and variables → Actions:

| Secret | Описание |
|---|---|
| `VPS_HOST` | IP тест-сервера |
| `VPS_USER` | Пользователь `deploy` на сервере |
| `VPS_SSH_KEY` | Приватный SSH ключ для деплоя |
| `TEST_DB_PASSWORD` | Пароль PostgreSQL |
| `YC_API_KEY` | Yandex AI Studio API key |
| `YC_FOLDER_ID` | Yandex Cloud folder ID |

---

## Локальная разработка без Docker

```bash
# Установить зависимости
uv sync --all-packages

# Запустить только PostgreSQL
docker compose -f docker-compose.yml -f docker-compose.test.yml --env-file .env.test up postgres -d

# Запустить platform-api напрямую
export $(cat .env.test | xargs)
uv run --package platform-api uvicorn platform_api.main:app --reload --port 8100

# Запустить pm-orchestrator напрямую
uv run --package pm-orchestrator python -m pm_orchestrator
```

---

## Полезные команды

```bash
# Запустить линтер
uv run ruff check .

# Автофикс линтера
uv run ruff check --fix .

# Форматирование
uv run ruff format .

# Тесты
uv run pytest -v

# Обновить зависимости
uv lock --upgrade

# Добавить зависимость в сервис
uv add --package platform-api httpx
```
