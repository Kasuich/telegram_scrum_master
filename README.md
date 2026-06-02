# PM Agent Platform

Мульти-агентная платформа для автоматизации работы Project Manager.

## Структура

```
packages/
  core/               # общая библиотека (модели, DB, утилиты)
services/
  platform-api/       # FastAPI — REST + A2A endpoint
  pm-orchestrator/    # LangGraph агент-оркестратор
```

## Локальный запуск

```bash
# Установить зависимости
uv sync --all-packages

# Запустить тест-окружение
cp .env.example .env.test
# заполнить .env.test
docker compose -f docker-compose.yml -f docker-compose.test.yml --env-file .env.test up --build
```

API доступен на http://localhost:8100

## CI/CD

| Событие | Что происходит |
|---|---|
| PR → `develop` | lint + tests |
| push → `develop` | lint + tests → deploy на test VPS |

## GitHub Secrets (environment: test)

| Secret | Описание |
|---|---|
| `VPS_HOST` | IP/hostname VPS |
| `VPS_USER` | пользователь deploy |
| `VPS_SSH_KEY` | приватный SSH ключ |
| `TEST_DB_PASSWORD` | пароль БД |
| `YC_API_KEY` | Yandex AI Studio API key |
| `YC_FOLDER_ID` | Yandex Cloud folder ID |

## Настройка VPS

```bash
bash scripts/vps-setup.sh "ssh-ed25519 AAAA... github-actions"
```
