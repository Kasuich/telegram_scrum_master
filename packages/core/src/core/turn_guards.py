"""Guards for a single user turn in action-only ReAct agents."""

from __future__ import annotations

from typing import Any

from core.assignee_resolver import extract_assignee_mention, resolve_assignee
from core.tracker import TrackerClient

_CREATE_MARKERS = (
    "создай",
    "заведи",
    "поставь",
    "оформи",
    "добавь задачу",
    "новая задача",
    "нужна задача",
    "сделай задачу",
)
_CLOSE_MARKERS = ("закрой", "закрыть", "заверши задачу", "close issue", "закрытие")


def normalize_text(text: str) -> str:
    return text.lower().replace("ё", "е")


def message_has_create_intent(text: str) -> bool:
    t = normalize_text(text)
    return any(m in t for m in _CREATE_MARKERS)


def message_has_close_intent(text: str) -> bool:
    t = normalize_text(text)
    return any(m in t for m in _CLOSE_MARKERS)


def created_issue_keys_in_turn(steps: list[dict[str, Any]], since_index: int) -> list[str]:
    keys: list[str] = []
    for step in steps[since_index:]:
        if step.get("kind") != "tool_result":
            continue
        if step.get("tool_name") != "tracker_create_issue":
            continue
        result = step.get("result") or {}
        key = result.get("key") or result.get("issue_key")
        if key:
            keys.append(str(key))
    return keys


async def check_turn_tool_guard(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    turn_user_message: str,
    steps: list[dict[str, Any]],
    steps_before_turn: int,
    queue_key: str,
) -> str | None:
    """
    Return error message to block tool execution, or None if allowed.
    """
    created = created_issue_keys_in_turn(steps, steps_before_turn)
    create_intent = message_has_create_intent(turn_user_message)
    close_intent = message_has_close_intent(turn_user_message)

    if tool_name == "tracker_close_issue" and created and create_intent and not close_intent:
        return (
            f"Запрещено закрывать задачу в том же запросе, где её создали ({', '.join(created)}). "
            "Пользователь просил СОЗДАТЬ, не закрыть. Заверши ход отчётом о создании."
        )

    if tool_name == "tracker_create_issue" and created:
        return (
            f"Уже создана задача {created[0]} в этом запросе. "
            "Одна задача на запрос; объедини темы в одном summary."
        )

    if tool_name != "tracker_create_issue" or not create_intent:
        return None

    llm_assignee = str(tool_args.get("assignee", "")).strip()
    if not llm_assignee:
        return None

    mention = extract_assignee_mention(turn_user_message)
    if not mention:
        return None

    async with TrackerClient() as client:
        expected = await resolve_assignee(mention, client, queue_key)
        actual = await resolve_assignee(llm_assignee, client, queue_key)

    if expected.score >= 0.42 and actual.score >= 0.42 and expected.login != actual.login:
        return (
            f"В запросе исполнитель «{mention}» → {expected.display} ({expected.login}), "
            f"а в tool call указан «{llm_assignee}» → {actual.display} ({actual.login}). "
            f"Используй assignee=\"{expected.login}\"."
        )

    return None
