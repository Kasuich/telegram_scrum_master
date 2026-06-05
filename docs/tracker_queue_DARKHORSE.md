# Tracker queue: DARKHORSE

Справочник полей очереди для `pm_agent`. Актуальные данные — после запуска:

```bash
cd packages/core
python scripts/audit_tracker_queue.py DARKHORSE
```

## Стандартные поля API (обычно доступны)

| Поле | Ключ API | Тулза |
|------|----------|--------|
| Название | `summary` | create / patch |
| Описание | `description` | create / patch |
| Исполнитель | `assignee` (login) | create / patch |
| Наблюдатели | `followers` | create; `tracker_update_followers` |
| Приоритет | `priority` | create / patch |
| Тип | `type` | create / patch |
| Теги | `tags` | create / patch |
| Дедлайн | `deadline` (YYYY-MM-DD) | create / patch |
| Story points | `storyPoints` | create / patch / custom_fields |
| Спринт | `sprint` | create / patch |
| Родитель | `parent` | create / patch; `tracker_link_issues` |
| Статус | transitions | `tracker_list_transitions` + `tracker_transition_issue` |

## Кастомные поля очереди

После `audit_tracker_queue.py` смотрите секцию **Local fields** в сгенерированном файле и передавайте id через `custom_fields` (JSON) в `tracker_create_issue` / `tracker_patch_issue`.
