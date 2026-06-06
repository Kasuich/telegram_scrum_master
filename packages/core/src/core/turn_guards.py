"""Guards for a single user turn in action-only ReAct agents."""

from __future__ import annotations

from typing import Any

from core.assignee_resolver import extract_assignee_mention, resolve_assignee
import os

from core.tracker import TrackerClient

_CREATE_MARKERS = (
    "создай",
    "заведи",
    "поставь",
    "добавь задачу",
    "новая задача",
    "нужна задача",
    "сделай задачу",
)
_CREATE_MARKERS_NARROW = _CREATE_MARKERS + ("оформи",)  # excludes backlog phrases

_BACKLOG_MARKERS = (
    "резюме",
    "саммари",
    "самари",
    "оформи доску",
    "разбей на задачи",
    "заведи эпик",
    "оформить доску",
    "бэклог",
    "backlog",
    "из лекции",
    "из созвона",
    "из встречи",
)
_CLOSE_MARKERS = ("закрой", "закрыть", "заверши задачу", "close issue", "закрытие")

_BACKLOG_ALLOWED = frozenset(
    {
        "backlog_plan",
        "tracker_apply_backlog_plan",
        "tracker_get_queue_meta",
    }
)


def normalize_text(text: str) -> str:
    return text.lower().replace("ё", "е")


def _backlog_min_summary_chars() -> int:
    raw = os.getenv("BACKLOG_MIN_SUMMARY_CHARS", "800")
    try:
        return int(raw)
    except ValueError:
        return 800


def message_has_backlog_intent(text: str) -> bool:
    t = normalize_text(text)
    min_chars = _backlog_min_summary_chars()
    if len(text.strip()) >= min_chars:
        return True
    return any(m in t for m in _BACKLOG_MARKERS)


def message_has_create_intent(text: str) -> bool:
    if message_has_backlog_intent(text):
        return False
    t = normalize_text(text)
    return any(m in t for m in _CREATE_MARKERS_NARROW)


def message_has_close_intent(text: str) -> bool:
    t = normalize_text(text)
    return any(m in t for m in _CLOSE_MARKERS)


def created_issue_keys_in_turn(steps: list[dict[str, Any]], since_index: int) -> list[str]:
    keys: list[str] = []
    for step in steps[since_index:]:
        if step.get("kind") != "tool_result":
            continue
        tool_name = step.get("tool_name")
        result = step.get("result") or {}
        if tool_name == "tracker_create_issue":
            key = result.get("key") or result.get("issue_key")
            if key:
                keys.append(str(key))
        elif tool_name == "tracker_apply_backlog_plan":
            for item in result.get("created") or []:
                k = item.get("key")
                if k:
                    keys.append(str(k))
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
    backlog_intent = message_has_backlog_intent(turn_user_message)
    created = created_issue_keys_in_turn(steps, steps_before_turn)
    create_intent = message_has_create_intent(turn_user_message)
    close_intent = message_has_close_intent(turn_user_message)

    if backlog_intent:
        if tool_name == "tracker_create_issue":
            return (
                "Длинное саммари / оформление доски: используй "
                "backlog_plan → tracker_apply_backlog_plan, не tracker_create_issue."
            )
        if tool_name in ("tracker_close_issue", "tracker_find_issues", "call_agent"):
            return (
                f"В режиме оформления доски «{tool_name}» не нужен. "
                "Цепочка: backlog_plan → tracker_apply_backlog_plan."
            )
        if tool_name == "tracker_apply_backlog_plan":
            for step in steps[steps_before_turn:]:
                if step.get("kind") != "tool_result":
                    continue
                if step.get("tool_name") != "tracker_apply_backlog_plan":
                    continue
                res = step.get("result") or {}
                if not res.get("error") and res.get("created_count", 0) > 0:
                    return "Доска уже создана в этом запросе. Заверши отчёт."
            return None
        if tool_name not in _BACKLOG_ALLOWED:
            pass  # allow backlog_plan and meta; block others below if duplicate apply

    if tool_name == "tracker_close_issue" and created and create_intent and not close_intent:
        return (
            f"Запрещено закрывать задачу в том же запросе, где её создали ({', '.join(created)}). "
            "Пользователь просил СОЗДАТЬ, не закрыть. Заверши ход отчётом о создании."
        )

    if tool_name == "tracker_create_issue" and created and not backlog_intent:
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

    # Only override LLM when the extracted mention is a confident team match
    if (
        expected.score >= 0.65
        and actual.score >= 0.42
        and expected.login != actual.login
    ):
        return (
            f"В запросе исполнитель «{mention}» → {expected.display} ({expected.login}), "
            f"а в tool call указан «{llm_assignee}» → {actual.display} ({actual.login}). "
            f"Используй assignee=\"{expected.login}\"."
        )

    return None
