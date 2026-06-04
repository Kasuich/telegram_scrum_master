"""
PM Agent — main agent for Yandex Tracker task management.

Importing this module registers all tracker tools in the ToolRegistry.
"""

from __future__ import annotations

import core.tracker_tools as _  # noqa: F401 — registers tracker tools
from core.agent import BaseAgent, LLMSettings

PROMPT = """Ты — PM-агент платформы управления проектами.
Ты помогаешь команде работать с задачами в Яндекс Трекере.

## Доступные инструменты
- tracker_get_issue — получить задачу по ключу (например, DARKHORSE-1)
- tracker_search_issues — найти задачи по запросу YQL
- tracker_create_issue — создать новую задачу (требует подтверждения)
- tracker_update_issue — обновить поля задачи (требует подтверждения)
- tracker_comment_issue — добавить комментарий к задаче
- tracker_close_issue — закрыть задачу (требует подтверждения)

## Правила работы
1. Перед созданием задачи уточни summary и очередь, если не указаны явно.
2. При поиске используй краткие YQL-запросы, например: Status: Open
3. Отвечай кратко и по делу на русском языке.
4. Если что-то не получилось — объясни ошибку простыми словами.
"""


class PMAgent(BaseAgent):
    """PM agent with full Yandex Tracker integration."""

    name = "pm_agent"
    description = "PM-ассистент для работы с задачами в Яндекс Трекере"
    prompt = PROMPT
    tools = [
        "tracker_get_issue",
        "tracker_search_issues",
        "tracker_create_issue",
        "tracker_update_issue",
        "tracker_comment_issue",
        "tracker_close_issue",
    ]
    llm_configs = [
        LLMSettings(model="gpt-oss-120b", temperature=0.3),
    ]
