# Tracker queue: DARKHORSE

Справочник полей очереди для `pm_agent` и `backlog_plan`.

Актуальные данные — после запуска:

```bash
cd packages/core
uv run python scripts/audit_tracker_queue.py DARKHORSE
```

## Типы задач (для backlog_plan)

Используйте ключи API (`type`), не отображаемые имена:

| Роль в backlog | Ключ API | Fallback если нет в очереди |
|----------------|----------|-----------------------------|
| Эпик | `epic` | `task` + тег `epic` |
| Story | `story` | `task` + тег `story` |
| Задача | `task` | `task` |
| Баг | `bug` | `bug` |

## Приоритеты

| Уровень | Ключ API |
|---------|----------|
| Критический | `critical` |
| Средний | `normal` |
| Низкий | `minor` |

## Story points

Стандартное поле API: `storyPoints` (число). Доступно на типах task/story, если включено в настройках очереди.

## Стандартные поля API

| Поле | Ключ API | Тулза |
|------|----------|--------|
| Название | `summary` | create / patch |
| Описание | `description` | create / patch |
| Исполнитель | `assignee` (login) | create / patch |
| Приоритет | `priority` | create / patch |
| Тип | `type` | create / patch |
| Теги | `tags` | create / patch |
| Дедлайн | `deadline` (YYYY-MM-DD) | create / patch |
| Story points | `storyPoints` | create / patch |
| Родитель | `parent` | create / patch; `tracker_link_issues` |

## Кастомные поля очереди

После `audit_tracker_queue.py` смотрите секцию **Local fields** в сгенерированном файле и передавайте id через `custom_fields` (JSON).
