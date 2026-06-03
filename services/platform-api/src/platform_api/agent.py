"""
PM Agent — main agent for the platform.

Importing this module registers all tracker tools in the ToolRegistry.
"""

from __future__ import annotations

# Register tracker tools — side effect populates ToolRegistry
import core.tracker_tools as _  # noqa: F401
from core.agent import BaseAgent, LLMSettings

PM_AGENT_PROMPT = """Ты — PM-агент платформы управления проектами.
Ты помогаешь команде работать с задачами в Яндекс Трекере.

## Доступные инструменты
- tracker_get_issue — получить задачу по ключу (например, DARKHORSE-1)
- tracker_search_issues — найти задачи по запросу YQL
- tracker_create_issue — создать новую задачу (требует подтверждения)
- tracker_update_issue — обновить поля задачи (требует подтверждения)
- tracker_comment_issue — добавить комментарий к задаче
- tracker_close_issue — закрыть задачу (требует подтверждения)

## Правила работы
1. Перед созданием задачи уточни у пользователя summary и очередь, если они не указаны.
2. При поиске используй краткие YQL-запросы, например: Status: Open
3. Отвечай кратко и по делу на русском языке.
4. Если что-то не получилось — объясни ошибку простыми словами.
"""


class PMAgent(BaseAgent):
    """Main PM agent with Yandex Tracker integration."""

    name = "pm_agent"
    description = "PM-ассистент для работы с задачами в Яндекс Трекере"
    prompt = PM_AGENT_PROMPT
    tools = [
        "tracker_get_issue",
        "tracker_search_issues",
        "tracker_create_issue",
        "tracker_update_issue",
        "tracker_comment_issue",
        "tracker_close_issue",
    ]
    llm_configs = [
        LLMSettings(model="yandexgpt", temperature=0.3),
        LLMSettings(model="yandexgpt-lite", temperature=0.3),
    ]
