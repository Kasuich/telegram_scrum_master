"""Guards for a single user turn in action-only ReAct agents."""

from __future__ import annotations

import os
import re
from typing import Any

from core.assignee_resolver import extract_assignee_mention, resolve_assignee
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
_CREATE_SPRINT_MARKERS = (
    "создай спринт",
    "заведи спринт",
    "создать спринт",
    "новый спринт",
    "create sprint",
)

# Specific board/backlog phrases — bare «резюме»/«саммари» omitted (too many false positives).
_BACKLOG_MARKERS = (
    "резюме лекции",
    "саммари лекции",
    "самари лекции",
    "оформи доску",
    "разбей на задачи",
    "заведи эпик",
    "оформить доску",
    "бэклог",
    "backlog",
    "из лекции",
    "из созвона",
    "из встречи",
    "заведи в трекер",
)
# Post-meeting reconciliation trigger (sync the board against discussion, not
# a from-scratch backlog). Must win over backlog intent in the stage router.
_MEETING_SYNC_MARKERS = (
    "синхронизируй доску",
    "синхронизация доски",
    "итоги встречи",
)
_CLOSE_MARKERS = (
    "закрой",
    "закрыть",
    "закрыто",
    "в закрыто",
    "заверши задачу",
    "close issue",
    "закрытие",
)

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


_STATUS_UPDATE_RE = re.compile(
    r"^([А-Яа-яA-Za-z][А-Яа-яA-Za-z.\-]*):\s",
    re.UNICODE,
)


def message_has_status_update_intent(text: str) -> bool:
    """Chat status line «Имя: новость по задаче» — update Tracker, not summarizer."""
    return bool(_STATUS_UPDATE_RE.match(text.strip()))


def message_has_backlog_intent(text: str) -> bool:
    if message_has_status_update_intent(text):
        return False
    t = normalize_text(text)
    min_chars = _backlog_min_summary_chars()
    if any(m in t for m in _BACKLOG_MARKERS):
        return True
    if len(text.strip()) >= min_chars:
        return True
    return False


def message_has_meeting_sync_intent(text: str) -> bool:
    """Post-meeting board sync: reconcile items against existing issues (update or create)."""
    t = normalize_text(text)
    return any(m in t for m in _MEETING_SYNC_MARKERS)


def message_has_create_intent(text: str) -> bool:
    if message_has_backlog_intent(text):
        return False
    t = normalize_text(text)
    return any(m in t for m in _CREATE_MARKERS_NARROW)


def message_has_create_sprint_intent(text: str) -> bool:
    t = normalize_text(text)
    return any(m in t for m in _CREATE_SPRINT_MARKERS)


def message_has_close_intent(text: str) -> bool:
    t = normalize_text(text)
    return any(m in t for m in _CLOSE_MARKERS)


def _turn_tool_results(steps: list[dict[str, Any]], since_index: int) -> list[dict[str, Any]]:
    return [
        step
        for step in steps[since_index:]
        if step.get("kind") == "tool_result" and step.get("tool_name")
    ]


def find_succeeded_in_turn(steps: list[dict[str, Any]], since_index: int) -> bool:
    for step in _turn_tool_results(steps, since_index):
        if step.get("tool_name") != "tracker_find_issues":
            continue
        result = step.get("result") or {}
        if result.get("error"):
            continue
        if result.get("not_found"):
            continue
        if result.get("count", 0) > 0:
            return True
        if result.get("issues"):
            return True
    return False


def summarizer_call_done_in_turn(steps: list[dict[str, Any]], since_index: int) -> bool:
    for step in _turn_tool_results(steps, since_index):
        if step.get("tool_name") != "call_agent":
            continue
        args = step.get("tool_args") or {}
        if args.get("target_agent") != "meeting_summarizer":
            continue
        result = step.get("result")
        if isinstance(result, str) and result.strip():
            return True
    return False


def created_issue_keys_in_turn(steps: list[dict[str, Any]], since_index: int) -> list[str]:
    keys: list[str] = []
    for step in steps[since_index:]:
        if step.get("kind") != "tool_result":
            continue
        tool_name = step.get("tool_name")
        result = step.get("result") or {}
        if tool_name in ("tracker_create_issue", "tracker_create_epic", "CreateIssue"):
            key = result.get("key") or result.get("issue_key")
            if key:
                keys.append(str(key))
        elif tool_name == "tracker_apply_backlog_plan":
            for item in result.get("created") or []:
                k = item.get("key")
                if k:
                    keys.append(str(k))
    return keys


async def check_create_assignee(
    *,
    tool_args: dict[str, Any],
    turn_user_message: str,
    queue_key: str,
) -> str | None:
    """Correct an assignee mismatch on create (async — resolves against the team).

    Returns an error string asking the LLM to use the resolved login, or None.
    Called by the runner only inside the INTAKE stage for tracker_create_issue,
    so the synchronous stage graph stays free of IO.
    """
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
    if expected.score >= 0.65 and actual.score >= 0.42 and expected.login != actual.login:
        return (
            f"В запросе исполнитель «{mention}» → {expected.display} ({expected.login}), "
            f"а в tool call указан «{llm_assignee}» → {actual.display} ({actual.login}). "
            f'Используй assignee="{expected.login}".'
        )

    return None


_MUTATING_STAGES = frozenset(
    {
        "INTAKE",
        "STATUS",
        "BOARD",
        "TRANSITION",
        "REORG",
    }
)

_READ_ONLY_STAGES = frozenset({"QUERY", "PROACTIVE", "HYGIENE", "DIALOG"})


def _last_find_result(steps: list[dict[str, Any]], since_index: int = 0) -> dict[str, Any] | None:
    for step in reversed(steps[since_index:]):
        if step.get("kind") == "tool_result" and step.get("tool_name") == "tracker_find_issues":
            result = step.get("result")
            if isinstance(result, dict):
                return result
    return None


def _issue_keys_used_in_turn(steps: list[dict[str, Any]], since_index: int = 0) -> set[str]:
    keys: set[str] = set()
    for step in steps[since_index:]:
        args = step.get("tool_args") or {}
        for field in ("issue_key", "key"):
            val = args.get(field)
            if val:
                keys.add(str(val))
    return keys


def _find_hint(payload: str) -> str:
    text = payload.strip()
    return text[:80] + ("…" if len(text) > 80 else "")


def clarification_needed(
    stage_id: Any,
    turn_steps: list[dict[str, Any]],
    payload: str,
    *,
    reason: str = "blocked",
) -> str | None:
    """Return a clarifying question for hard blockers, or None.

    Read-only stages never clarify. Soft fields (priority/deadline) are not blockers.
    """
    from core.stage_graph import (
        StageId,
        apply_backlog_succeeded,
        create_sprint_succeeded,
        get_stage,
        transition_or_close_succeeded,
    )

    if stage_id is None:
        return None
    if isinstance(stage_id, StageId):
        stage_value = stage_id.value
    else:
        stage_value = str(stage_id)
    if stage_value in _READ_ONLY_STAGES:
        return None

    hint = _find_hint(payload)

    if stage_value in ("STATUS", "TRANSITION", "REORG"):
        find_result = _last_find_result(turn_steps, 0)
        if find_result is None:
            if reason == "max_iter":
                return f"Не нашёл задачу по «{hint}». Уточни ключ задачи или исполнителя."
            return None
        if find_result.get("error") or find_result.get("not_found"):
            return f"Не нашёл задачу по «{hint}». Уточни ключ задачи или исполнителя."
        count = int(find_result.get("count") or 0)
        if count == 0 and not find_result.get("issues"):
            return f"Не нашёл задачу по «{hint}». Уточни ключ задачи или исполнителя."
        if count > 1 and not _issue_keys_used_in_turn(turn_steps, 0):
            return f"Нашёл несколько задач по «{hint}». Уточни ключ задачи (например DARKHORSE-12)."

    if stage_value == "TRANSITION":
        transitions_seen = False
        transitions_empty = False
        for step in turn_steps:
            if step.get("kind") != "tool_result":
                continue
            if step.get("tool_name") != "tracker_list_transitions":
                continue
            transitions_seen = True
            result = step.get("result") or {}
            if result.get("error") or not result.get("transitions"):
                transitions_empty = True
        if transitions_seen and transitions_empty and not transition_or_close_succeeded(turn_steps):
            return "Не удалось определить целевой статус. Уточни, в какой статус перевести задачу."

    if reason != "max_iter":
        return None

    stage = get_stage(stage_value)
    if stage is not None and stage.is_terminal(turn_steps):
        return None

    if stage_value == "INTAKE":
        created = created_issue_keys_in_turn(turn_steps, 0)
        if not created and not create_sprint_succeeded(turn_steps):
            return (
                f"Не удалось создать задачу по «{hint}». "
                "Уточни исполнителя и краткое название задачи."
            )
    elif stage_value == "BOARD":
        if not apply_backlog_succeeded(turn_steps):
            return (
                f"Не удалось оформить доску по «{hint}». "
                "Пришли более структурированное саммари или уточни эпик."
            )
    elif stage_value in _MUTATING_STAGES:
        if stage_value == "TRANSITION" and transition_or_close_succeeded(turn_steps):
            return None
        if stage_value == "STATUS":
            return f"Не удалось обновить статус по «{hint}». Уточни ключ задачи или исполнителя."
        if stage_value == "REORG":
            return f"Не удалось выполнить перестройку по «{hint}». Уточни ключ задачи."
        return f"Не удалось выполнить действие по «{hint}». Уточни запрос."

    return None


async def check_turn_tool_guard(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    turn_user_message: str,
    steps: list[dict[str, Any]],
    steps_before_turn: int,
    queue_key: str,
) -> str | None:
    """Thin shim over the stage graph (single source of truth for guards).

    Kept for backward compatibility: classifies the message with the rules-only
    path, runs the stage's ``check_tool`` over the turn slice, and adds the
    async assignee correction for INTAKE creates. The runner uses the stage
    graph directly; this shim keeps existing call sites and tests working.
    """
    from core.stage_graph import StageId, get_stage
    from core.stage_router import detect_stage_rules

    sid = detect_stage_rules(turn_user_message)
    if sid is None:
        return None
    stage = get_stage(sid)
    if stage is None:
        return None

    turn_slice = steps[steps_before_turn:]
    decision = stage.check_tool(tool_name, tool_args, turn_slice)
    if not decision.allow:
        return decision.reason

    if sid == StageId.INTAKE and tool_name == "tracker_create_issue":
        return await check_create_assignee(
            tool_args=tool_args,
            turn_user_message=turn_user_message,
            queue_key=queue_key,
        )
    return None
