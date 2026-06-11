"""
ReAct loop with autonomy gate and optional DB persistence.

Flow per iteration:
  1. Call LLM with current conversation history
  2a. Tool call → autonomy gate:
      - risk in auto_risk  → execute immediately, feed result back, continue
      - risk in confirm_risk → create Confirm, interrupt, return pending_confirm
  2b. Text reply → return AgentResult(reply=...)
  3. Repeat up to max_iterations

Session state (messages + trace steps) is stored in DB when a session is
provided, or in memory for testing / standalone use.

Resume flow:
  resume(confirm_id, approved) → load session from Trace → execute or reject
  tool → continue the ReAct loop from where it left off.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.agent import BaseAgent
from core.assignee_resolver import best_user_match, load_team_users
from core.config import RuntimeConfig
from core.exceptions import AgentError
from core.goal import (
    GoalItem,
    GoalPlan,
    build_goal_plan,
    deserialize_plan,
    serialize_plan,
)
from core.invocation import (
    InvocationContext,
    format_transport_context_for_prompt,
    normalize_invocation_context,
    reset_current_invocation_context,
    set_current_invocation_context,
)
from core.llm import LLMClient, Message
from core.stage_graph import Stage, StageId, get_stage
from core.stage_router import detect_stage
from core.tools import get_registry
from core.turn_guards import (
    check_create_assignee,
    clarification_needed,
    created_issue_keys_in_turn,
    message_has_create_sprint_intent,
)

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ITERATIONS = 12

_TOOL_LABELS = {
    "tracker_create_issue": "Создание задачи в Трекере",
    "tracker_patch_issue": "Обновление задачи",
    "tracker_update_issue": "Изменение задачи",
    "tracker_close_issue": "Закрытие задачи",
    "tracker_close_issues": "Закрытие задач",
    "tracker_transition_issue": "Смена статуса задачи",
    "tracker_move_issues_to_in_progress": "Перевод задач в работу",
    "tracker_comment_issue": "Комментарий к задаче",
    "tracker_link_issues": "Связывание задач",
    "tracker_create_epic": "Создание эпика",
    "tracker_open_epic": "Открытие эпика",
    "tracker_close_epic": "Закрытие эпика",
    "tracker_create_sprint": "Создание спринта",
    "tracker_open_sprint": "Открытие спринта",
    "tracker_close_sprint": "Закрытие спринта",
    "tracker_rollover_sprint": "Закрытие спринта и перенос задач",
    "tracker_apply_backlog_plan": "Создание задач из плана",
    "tracker_add_issues_to_sprint": "Добавление задач в спринт",
    "CreateIssue": "Создание задачи в Трекере",
    "UpdateIssue": "Изменение задачи",
    "ChangeIssueStatus": "Смена статуса задачи",
    "CreateComment": "Комментарий к задаче",
    "BulkUpdate": "Массовое изменение задач",
    "BulkTransition": "Массовая смена статуса",
    "BulkMove": "Массовый перенос задач",
    "DeleteGoal": "Удаление цели",
    "schedule_task": "Планирование cron-задачи",
}
_SESSION_MESSAGE_WINDOW = 10
_SESSION_CONTEXT_FALLBACK_LINES = 24
_SESSION_CONTEXT_SYSTEM = (
    "Ты обновляешь долговременную рабочую память PM-агента по истории чата. "
    "Твоя задача не пересказать диалог, а собрать профессиональный operational context, "
    "который поможет агенту принимать следующие решения без повторного чтения всей истории.\n\n"
    "Сформируй сжатый контекст на русском языке объёмом 20-30 предложений. "
    "Пиши плотно, фактически и без воды. Не используй вступления вроде "
    "«в обсуждении говорилось» и не описывай процесс сжатия.\n\n"
    "Приоритет содержания, от более важного к менее важному:\n"
    "1. Кто входит в команду, какие у людей роли, зоны ответственности, логины, алиасы, "
    "устойчивые связи между именами и задачами.\n"
    "2. Над какими проектами, эпиками, сервисами, интеграциями и направлениями "
    "команда реально работает.\n"
    "3. Какие договорённости и рабочие правила уже приняты: naming, очереди, маршруты, "
    "workflow, транспорт, ограничения по прод/тесту, способы деплоя и эксплуатации.\n"
    "4. Какие незавершённые хвосты, риски, блокеры, технические долги и спорные "
    "решения сейчас открыты.\n"
    "5. Какие артефакты и объекты нужно помнить дальше: issue keys, service names, server roles, "
    "env vars, chat ids, важные URL, feature flags, branch names.\n"
    "6. Какие недавние действия уже были выполнены и что не нужно предлагать повторно.\n\n"
    "Строго исключай:\n"
    "- болтовню, эмоции, шутки и одноразовые фразы;\n"
    "- дословный пересказ каждой реплики;\n"
    "- предположения, которых нет в сообщениях;\n"
    "- устаревшие детали, если новые сообщения им противоречат.\n\n"
    "Если данных мало, верни короткий, но всё равно полезный рабочий контекст "
    "только из подтверждённых фактов."
)

# Stable namespace for deriving a UUID from an arbitrary session string
# (e.g. a Telegram chat id) so it can be stored in Trace.session_id.
_SESSION_NS = uuid.UUID("6f9b9af4-7a3e-5c2d-9b1a-0e1f2a3b4c5d")


def _session_uuid(session_id: str) -> uuid.UUID:
    """Coerce a session identifier into a UUID.

    If ``session_id`` is already a valid UUID string it is used as-is;
    otherwise a deterministic UUIDv5 is derived from it. This lets callers
    use opaque strings (chat ids, "s1", ...) while the DB stores UUIDs.
    """
    try:
        return uuid.UUID(session_id)
    except (ValueError, AttributeError, TypeError):
        return uuid.uuid5(_SESSION_NS, str(session_id))


@dataclass
class _RunCtx:
    """Per-call context: persistence + effective-config overrides.

    Threaded through a single ``invoke``/``resume`` call so that concurrent
    calls on a shared :class:`ReActRunner` never clobber each other's session.
    ``db_session is None`` selects the in-memory store.

    Optional effective-config fields (``None`` → fall back to class defaults):
    - ``effective_prompt``: overrides ``agent.prompt`` as the system message.
    - ``effective_runtime_config``: overrides ``runner.runtime_config`` for the
      Autonomy Gate (auto_risk / confirm_risk / always_confirm_tools).
    """

    db_session: Any | None = None
    team_id: str | None = None
    effective_prompt: str | None = None
    effective_runtime_config: Any | None = None  # RuntimeConfig | None
    invocation_context: InvocationContext | None = None


# ---------------------------------------------------------------------------
# Public data models
# ---------------------------------------------------------------------------


class PendingConfirm(BaseModel):
    """A tool call that requires user approval before execution."""

    confirm_id: str
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    risk: Literal["low", "medium", "high"]
    prompt: str


class AgentResult(BaseModel):
    """Outcome of a single invoke() or resume() call."""

    reply: str | None = None
    clarification: str | None = None
    pending_confirm: PendingConfirm | None = None
    session_id: str
    steps: list[dict[str, Any]] = Field(default_factory=list)


@dataclass
class ScenarioOutcome:
    """Internal result of executing one scenario in the turn plan."""

    kind: Literal["done", "needs_confirm", "clarification", "max_iter"]
    turn_steps: list[dict[str, Any]] | None = None
    agent_result: AgentResult | None = None
    clarification: str | None = None
    reply: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _step(kind: str, **kwargs: Any) -> dict[str, Any]:
    return {"kind": kind, "ts": _now(), **kwargs}


def _session_context_message(summary: str) -> dict[str, str]:
    return {
        "role": "system",
        "content": (
            "Session context summary. Use it as durable memory, "
            "but prefer newer messages when they conflict.\n\n"
            f"{summary}"
        ),
    }


def _render_messages_for_summary(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in messages:
        role = str(item.get("role", "user")).upper()
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _fallback_session_context(
    existing_summary: str | None,
    older_messages: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    if existing_summary:
        lines.extend(
            sentence.strip() for sentence in existing_summary.splitlines() if sentence.strip()
        )

    for item in older_messages:
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        role = "Пользователь" if item.get("role") == "user" else "Ассистент"
        lines.append(f"{role}: {content}")
        if len(lines) >= _SESSION_CONTEXT_FALLBACK_LINES:
            break

    return "\n".join(lines[:_SESSION_CONTEXT_FALLBACK_LINES])


def _tool_result_message(tool_name: str, result: Any, *, action_only: bool = False) -> str:
    if action_only:
        return (
            f"Инструмент «{tool_name}» выполнен. Результат: {result}. "
            "Если запрос пользователя уже выполнен, ответь БЕЗ tool calls "
            "(будет автоматический отчёт). "
            "Иначе — один следующий tool call. Не повторяй то же действие с теми же аргументами."
        )
    return (
        f"Инструмент «{tool_name}» выполнен успешно. Результат: {result}. "
        "Сообщи пользователю о результате кратко и по-русски."
    )


def _tool_rejected_message(tool_name: str, *, action_only: bool = False) -> str:
    if action_only:
        return (
            f"Пользователь отклонил «{tool_name}». "
            "Перейди к следующему действию из запроса или заверши отчёт. Без вопросов."
        )
    return (
        f"Пользователь отклонил вызов инструмента «{tool_name}». "
        "Объясни, что хотел сделать, и спроси как поступить иначе."
    )


def _tool_error_message(tool_name: str, error: str, *, action_only: bool = False) -> str:
    if action_only:
        return (
            f"Инструмент «{tool_name}» ошибка: {error}. "
            "Исправь аргументы и повтори tool call или выполни следующее действие. Без вопросов."
        )
    return f"Инструмент «{tool_name}» завершился с ошибкой: {error}. Сообщи об ошибке пользователю."


def _progress_checkpoint(
    goal_item: GoalItem | None,
    turn_steps: list[dict[str, Any]],
    *,
    iterations_left: int,
) -> str:
    """Build a compact plan/observe/reflect checkpoint for the next LLM pass."""
    calls: list[str] = []
    errors: list[str] = []
    for step in turn_steps:
        kind = step.get("kind")
        name = str(step.get("tool_name") or "")
        if kind == "tool_result" and name:
            calls.append(name)
        elif kind == "tool_error" and name:
            errors.append(f"{name}: {str(step.get('error') or '')[:160]}")

    intent = (goal_item.intent if goal_item else "") or (goal_item.payload if goal_item else "")
    success = goal_item.success_criteria if goal_item else ""
    return (
        "\n\nREFLECTION CHECKPOINT\n"
        f"Цель: {intent or 'выполнить запрос пользователя'}\n"
        f"Критерий успеха: {success or 'запрос выполнен и результат проверен'}\n"
        f"Успешные вызовы: {', '.join(calls[-6:]) or 'нет'}\n"
        f"Ошибки: {'; '.join(errors[-3:]) or 'нет'}\n"
        f"Осталось итераций: {iterations_left}\n"
        "Переоцени план по наблюдениям. Если цель достигнута, заверши ответом без tool call. "
        "Если нет, выбери один лучший следующий tool call. Не повторяй уже успешный вызов "
        "с теми же аргументами. Проверяй результат записи чтением только когда это полезно."
    )


def _format_action_tool_line(tool_name: str, result: dict[str, Any]) -> str:
    if tool_name == "CreateIssue":
        key = result.get("key") or result.get("issue_key", "")
        return f"Создана {key} «{result.get('summary', '')}»"
    if tool_name == "UpdateIssue":
        key = result.get("key") or result.get("issue_key", "")
        return f"Обновлена {key}"
    if tool_name == "ChangeIssueStatus":
        key = result.get("key") or result.get("issue_key", "")
        return f"Статус изменён: {key}"
    if tool_name == "CreateComment":
        key = result.get("issue_key") or result.get("key", "")
        return f"Добавлен комментарий к {key}"
    if tool_name == "GetIssue":
        key = result.get("key", "")
        summary = result.get("summary", "")
        return f"{key} «{summary}»".strip()
    if tool_name == "GetIssues":
        issues = result.get("issues")
        if isinstance(issues, list):
            return f"Найдено задач: {len(issues)}"
    if tool_name in {"BulkUpdate", "BulkTransition", "BulkMove"}:
        bulk_id = result.get("bulkchange_id") or result.get("id", "")
        return f"{tool_name}: запущена операция {bulk_id}".strip()
    if tool_name == "tracker_close_issue":
        issue = result.get("issue") or {}
        key = issue.get("key") or result.get("issue_key", "?")
        return f"Закрыта {key} «{issue.get('summary', '')}» — {issue.get('status', '')}"
    if tool_name in ("tracker_find_issues", "tracker_search_issues"):
        if result.get("not_found") or result.get("count", 0) == 0:
            return "Задача не найдена"
        issues = result.get("issues") or []
        parts = [f"{i.get('key')} «{i.get('summary')}» ({i.get('status')})" for i in issues[:5]]
        return "Найдено: " + "; ".join(parts)
    if tool_name in ("tracker_create_issue", "tracker_patch_issue", "tracker_update_issue"):
        key = result.get("key") or result.get("issue_key", "")
        who = result.get("assignee", "")
        verb = "Создана" if tool_name == "tracker_create_issue" else "Обновлена"
        line = f"{verb} {key} «{result.get('summary', '')}»"
        if who:
            line += f", исполнитель {who}"
        return line
    if tool_name == "tracker_create_sprint":
        name = result.get("name") or ""
        sprint_id = result.get("id") or "?"
        board = result.get("board") or result.get("board_id") or "?"
        start = result.get("start_date") or "?"
        end = result.get("end_date") or "?"
        return f"Создан спринт {sprint_id} «{name}» на доске {board}: {start} — {end}"
    if tool_name in ("tracker_open_sprint", "tracker_close_sprint"):
        name = result.get("name") or ""
        sprint_id = result.get("id") or "?"
        action = "Открыт" if tool_name == "tracker_open_sprint" else "Закрыт"
        return f"{action} спринт {sprint_id} «{name}»"
    if tool_name == "tracker_rollover_sprint":
        old_sprint = result.get("old_sprint") or {}
        new_sprint = result.get("new_sprint") or {}
        line = (
            f"Закрыт спринт «{old_sprint.get('name', '?')}», создан "
            f"«{new_sprint.get('name', '?')}», перенесено задач: {result.get('moved_count', 0)}"
        )
        if result.get("error_count"):
            line += f", ошибок: {result.get('error_count')}"
        if result.get("close_error"):
            line += f"; закрытие старого спринта не удалось: {result.get('close_error')}"
        return line
    if tool_name == "tracker_create_epic":
        key = result.get("key") or result.get("issue_key", "")
        return f"Создан эпик {key} «{result.get('summary', '')}»"
    if tool_name in ("tracker_open_epic", "tracker_close_epic"):
        issue = result.get("issue") or result
        key = issue.get("key") or result.get("issue_key", "")
        action = "Открыт" if tool_name == "tracker_open_epic" else "Закрыт"
        return f"{action} эпик {key} «{issue.get('summary', '')}»"
    if tool_name == "tracker_add_issues_to_sprint":
        sprint = result.get("sprint_name") or result.get("sprint_id") or "?"
        n = result.get("updated_count", 0)
        err = result.get("error_count", 0)
        line = f"В спринт «{sprint}» добавлено задач: {n}"
        if err:
            line += f", ошибок: {err}"
        return line
    if tool_name == "tracker_update_followers":
        key = result.get("key") or result.get("issue_key", "")
        return f"Наблюдатели обновлены: {key}"
    if tool_name == "tracker_move_issues_to_in_progress":
        n = result.get("updated_count", 0)
        err = result.get("error_count", 0)
        line = f"Переведено в работу: {n}"
        if err:
            line += f", ошибок: {err}"
        return line
    if tool_name == "tracker_close_issues":
        n = result.get("closed_count", 0)
        err = result.get("error_count", 0)
        line = f"Закрыто задач: {n}"
        if err:
            line += f", ошибок: {err}"
        return line
    if tool_name == "tracker_comment_issue":
        key = result.get("issue_key", "")
        text = (result.get("text") or "")[:120]
        return f"Комментарий к {key}: {text}"
    if tool_name == "backlog_plan":
        if result.get("error"):
            return f"backlog_plan: {result['error']}"
        return (
            f"План: epic={'да' if result.get('create_epic') else 'нет'}, "
            f"stories={result.get('stories_count', 0)}, "
            f"tasks={result.get('tasks_count', 0)}"
        )
    if tool_name == "tracker_apply_backlog_plan":
        if result.get("error"):
            return f"tracker_apply_backlog_plan: {result['error']}"
        epic = result.get("epic_key")
        n = result.get("created_count", 0)
        skip_n = result.get("skipped_count", 0)
        err_n = result.get("error_count", 0)
        if n == 0 and err_n == 0 and skip_n == 0:
            return (
                "Доска: не создано ни одной задачи, план пуст или backlog_plan завершился с ошибкой"
            )
        line = f"Доска: создано {n} задач"
        if epic:
            line += f", эпик {epic}"
        if skip_n:
            line += f", пропущено дублей {skip_n}"
            skipped = result.get("skipped") or []
            if skipped:
                examples = ", ".join(f"{s.get('key')}" for s in skipped[:2] if s.get("key"))
                if examples:
                    line += f" ({examples})"
        if err_n:
            line += f", ошибок {err_n}"
        tree = result.get("tree") or []
        if tree:
            line += "\n" + "\n".join(tree[:6])
        critical = result.get("critical") or []
        if critical:
            crit_parts = [f"{c.get('key')} до {c.get('deadline', '?')}" for c in critical[:3]]
            line += "\nCritical: " + "; ".join(crit_parts)
        return line
    key = result.get("key") or result.get("issue_key", "")
    if key:
        return f"{tool_name}: {key}"
    if result.get("error"):
        return f"{tool_name}: {result['error']}"
    return f"{tool_name}: выполнено"


def _is_duplicate_tool_success(
    turn_steps: list[dict[str, Any]], tool_name: str, tool_args: dict[str, Any]
) -> bool:
    for step in turn_steps:
        if step.get("kind") != "tool_result":
            continue
        if step.get("tool_name") != tool_name:
            continue
        if step.get("tool_args") == tool_args:
            return True
    return False


def _should_auto_finalize_turn(turn_steps: list[dict[str, Any]]) -> bool:
    """Stop looping when enough writes succeeded on one issue."""
    results = [s for s in turn_steps if s.get("kind") == "tool_result"]
    if len(results) >= 4:
        return True
    if len(results) >= 2:
        last = results[-2:]
        keys = {s.get("tool_args", {}).get("issue_key") for s in last}
        if len(keys) == 1 and None not in keys:
            names = {s.get("tool_name") for s in last}
            if names <= {
                "tracker_patch_issue",
                "tracker_update_issue",
                "tracker_update_followers",
                "tracker_comment_issue",
            }:
                return True
    apply_done = any(
        s.get("kind") == "tool_result" and s.get("tool_name") == "tracker_apply_backlog_plan"
        for s in turn_steps
    )
    if apply_done:
        return True
    if len(results) >= 3:
        write_results = [
            s
            for s in results
            if s.get("tool_name")
            in (
                "tracker_patch_issue",
                "tracker_update_issue",
                "tracker_update_followers",
                "tracker_comment_issue",
            )
        ]
        if len(write_results) >= 2:
            return True
    return False


def _goal_terminal_for_stage(
    stage: Stage | None,
    goal_item: GoalItem | None,
    turn_steps: list[dict],
) -> bool:
    """Check if the goal is likely met for the current stage."""
    if stage is None:
        return False
    if stage.id == StageId.QUERY:
        tool_results = [
            s for s in turn_steps if s.get("kind") == "tool_result" and s.get("tool_name")
        ]
        if not tool_results:
            return False
        return any(
            isinstance(s.get("result"), dict) and not s.get("result", {}).get("error")
            for s in tool_results
        )
    return stage.is_terminal(turn_steps)


_GOAL_JUDGE_SYSTEM = (
    "Ты — судья PM-агента. Реши, достигнута ли цель по результатам инструментов.\n"
    "Ответь СТРОГО одно слово: YES / NO / NEEDS_MORE.\n"
    "Если NO или NEEDS_MORE — через | укажи что не хватает.\n"
    "Пример: NO|нет данных по story points"
)


@dataclass
class GoalVerdict:
    met: bool
    reason: str
    tier: int  # 1=deterministic, 2=LLM judge


async def _goal_met(
    goal_item: GoalItem,
    turn_steps: list[dict],
    *,
    use_llm: bool = True,
) -> GoalVerdict:
    """Two-tier goal verification: deterministic first, LLM judge when uncertain."""
    stage_id = goal_item.stage
    tool_results = [s for s in turn_steps if s.get("kind") == "tool_result"]

    if stage_id == StageId.INTAKE:
        if created_issue_keys_in_turn(turn_steps, 0):
            return GoalVerdict(met=True, reason="issue_created", tier=1)
    elif stage_id == StageId.STATUS:
        from core.stage_graph import comment_succeeded

        if comment_succeeded(turn_steps):
            return GoalVerdict(met=True, reason="comment_succeeded", tier=1)
    elif stage_id == StageId.TRANSITION:
        from core.stage_graph import transition_or_close_succeeded

        if transition_or_close_succeeded(turn_steps):
            return GoalVerdict(met=True, reason="transition_succeeded", tier=1)
    elif stage_id == StageId.QUERY:
        has_data = any(
            isinstance(s.get("result"), dict) and not s.get("result", {}).get("error")
            for s in tool_results
        )
        metric = (goal_item.entities or {}).get("metric")
        if has_data and not metric:
            return GoalVerdict(met=True, reason="query_data_present", tier=1)
        # Metric (SP/count/load) requested → let judge verify the answer contains it
        if has_data and metric and not use_llm:
            return GoalVerdict(met=True, reason="query_data_present_no_llm", tier=1)
        # Otherwise fall through to tier-2 LLM judge below

    if not use_llm or not tool_results:
        return GoalVerdict(met=False, reason="no_data", tier=1)

    try:
        client = LLMClient(
            model="google/gemini-3.1-flash-lite",
            provider="openrouter",
            temperature=0.0,
            max_tokens=32,
            max_retries=0,
        )
        results_summary = str(turn_steps)[:2000]
        judge_prompt = (
            f"Цель: {goal_item.success_criteria or goal_item.intent}\n"
            f"Результаты инструментов: {results_summary}"
        )
        resp = await client.complete(
            [
                Message(role="system", content=_GOAL_JUDGE_SYSTEM),
                Message(role="user", content=judge_prompt),
            ]
        )
        await client.close()
        raw = (resp.content or "").strip().upper()
        if raw.startswith("YES"):
            return GoalVerdict(met=True, reason=raw, tier=2)
        return GoalVerdict(met=False, reason=raw, tier=2)
    except Exception:
        return GoalVerdict(met=False, reason="judge_error", tier=2)


def _assumptions_line(steps: list[dict[str, Any]]) -> str:
    """Surface fields the agent filled on create (self-check transparency).

    INTAKE/BOARD self-check infers missing fields and fills them; this reports
    what was assumed so a human can verify and correct.
    """
    for step in steps:
        if step.get("kind") != "tool_result":
            continue
        if step.get("tool_name") != "tracker_create_issue":
            continue
        result = step.get("result") or {}
        if not isinstance(result, dict) or result.get("error"):
            continue
        parts: list[str] = []
        if result.get("assignee"):
            parts.append(f"исполнитель {result['assignee']}")
        if result.get("priority"):
            parts.append(f"приоритет {result['priority']}")
        if result.get("deadline"):
            parts.append(f"дедлайн {result['deadline']}")
        if result.get("story_points") is not None:
            parts.append(f"оценка {result['story_points']}")
        if parts:
            return "Предположения: " + ", ".join(parts)
    return ""


def _build_action_report(steps: list[dict[str, Any]]) -> str:
    """Compact report from tool steps (for action_only agents)."""
    lines: list[str] = []
    for step in steps:
        kind = step.get("kind")
        if kind == "tool_result":
            result = step.get("result") or {}
            if isinstance(result, dict):
                lines.append(_format_action_tool_line(step.get("tool_name", "tool"), result))
        elif kind == "tool_error":
            lines.append(f"ОШИБКА {step.get('tool_name')}: {str(step.get('error', ''))[:200]}")
        elif kind == "confirm_wait":
            lines.append(
                f"ОЖИДАЕТ ПОДТВЕРЖДЕНИЯ: {step.get('tool_name')} "
                f"(confirm_id={step.get('confirm_id')})"
            )
    if not lines:
        return ""
    errors = [ln for ln in lines if ln.startswith("ОШИБКА")]
    board = [ln for ln in lines if ln.startswith("Доска:")]
    if board:
        return board[-1]
    created = [ln for ln in lines if ln.startswith("Создана")]
    if created:
        assumptions = _assumptions_line(steps)
        return f"{created[-1]}. {assumptions}" if assumptions else created[-1]
    updated = [ln for ln in lines if ln.startswith(("Обновлена", "Наблюдатели"))]
    comments = [ln for ln in lines if ln.startswith("Комментарий")]
    if updated and comments:
        return f"{updated[-1]}. {comments[-1]}"
    if updated:
        return updated[-1]
    if comments:
        return comments[-1]
    for step in steps:
        if step.get("kind") != "tool_result" or step.get("tool_name") != "call_agent":
            continue
        delegated = step.get("result")
        if isinstance(delegated, str) and delegated.strip():
            return delegated.strip()
    for line in reversed(lines):
        if line.startswith(("Найдено:", "Закрыта")):
            return line
    if errors:
        return errors[-1]
    for line in reversed(lines):
        if line == "Задача не найдена":
            return line
    return lines[-1]


_READ_VOICE_STAGES = {StageId.QUERY}

# YandexGPT content-safety filter phrases that leak into LLM output.
# When detected, we suppress the response and use a safe fallback instead.
_SAFETY_FILTER_PHRASES = (
    "Я не могу обсуждать эту тему",
    "Давайте поговорим о чём-нибудь ещё",
    "не могу помочь с этим запросом",
    "Цель запроса",
    "Нет необходимости в дополнительных действиях",
    "Достигнут лимит итераций",
)


def _is_safety_filtered(text: str) -> bool:
    return any(phrase in text for phrase in _SAFETY_FILTER_PHRASES)


def _issue_field(issue: dict[str, Any], name: str) -> str:
    value = issue.get(name)
    if isinstance(value, dict):
        return str(value.get("display") or value.get("name") or value.get("key") or "").strip()
    return str(value or "").strip()


def _format_read_tool_reply(tool_name: str, result: Any) -> str | None:
    """Render simple issue reads without another LLM pass."""
    if tool_name == "GetIssue" and isinstance(result, dict):
        issues = [result]
    elif tool_name == "GetIssues":
        if isinstance(result, list):
            issues = result
        elif isinstance(result, dict):
            raw_issues = result.get("issues") or result.get("values") or result.get("items")
            issues = raw_issues if isinstance(raw_issues, list) else []
        else:
            issues = []
    else:
        return None

    issues = [issue for issue in issues if isinstance(issue, dict)]
    if not issues:
        return "Задачи не найдены."

    lines: list[str] = []
    for issue in issues[:20]:
        key = _issue_field(issue, "key")
        summary = _issue_field(issue, "summary")
        status = _issue_field(issue, "status")
        assignee = _issue_field(issue, "assignee")
        title = " ".join(part for part in (key, f"«{summary}»" if summary else "") if part)
        details = ", ".join(part for part in (status, assignee) if part)
        lines.append(f"- {title}" + (f" ({details})" if details else ""))
    if len(issues) > 20:
        lines.append(f"- …и ещё {len(issues) - 20}")
    return "\n".join(lines)


def _action_only_final_reply(
    steps: list[dict[str, Any]],
    llm_text: str,
    had_tool: bool,
    *,
    stage_id: StageId | None = None,
) -> str:
    if stage_id in _READ_VOICE_STAGES:
        if llm_text.strip() and not _is_safety_filtered(llm_text):
            return llm_text.strip()
        # The model has explicitly ended the turn. Recover a useful answer from
        # the latest simple read only when its final text is empty or internal.
        for step in reversed(steps):
            if step.get("kind") != "tool_result":
                continue
            reply = _format_read_tool_reply(
                str(step.get("tool_name") or ""),
                step.get("result"),
            )
            if reply:
                return reply
        if had_tool:
            return "Получил данные из трекера. Попробуй переформулировать запрос конкретнее."
    report = _build_action_report(steps)
    if report:
        return report
    if not had_tool:
        return "Действия не выполнены."
    return llm_text


def _successful_tool_names(turn_steps: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for step in turn_steps:
        if step.get("kind") != "tool_result":
            continue
        result = step.get("result")
        if isinstance(result, dict) and result.get("error"):
            continue
        names.add(str(step.get("tool_name") or ""))
    return names


def _freeform_unfinished_action(
    user_message: str,
    stage_id: StageId | None,
    turn_steps: list[dict[str, Any]],
) -> str | None:
    """Return feedback when an explicit user action is still missing."""
    successful = _successful_tool_names(turn_steps)
    normalized = user_message.lower().replace("ё", "е")

    if stage_id == StageId.QUERY and not successful.intersection(
        {
            "GetIssue",
            "GetIssues",
            "GetIssueLinks",
            "GetProject",
            "GetPortfolio",
            "GetGoal",
            "SearchEntities",
            "tracker_board_snapshot",
        }
    ):
        return (
            "Запрос требует актуальных данных. Не отвечай из памяти или истории: "
            "вызови подходящий read-инструмент."
        )

    create_done = bool(successful.intersection({"CreateIssue", "tracker_create_issue"}))
    if not create_done:
        return None

    status_requested = "статус" in normalized and any(
        marker in normalized for marker in ("в работе", "в работу", "in progress", "inprogress")
    )
    if status_requested and not successful.intersection(
        {"ChangeIssueStatus", "tracker_transition_issue"}
    ):
        return (
            "Пользователь явно попросил перевести созданную задачу в работу. "
            "Не описывай следующий вызов текстом: вызови ChangeIssueStatus сейчас."
        )

    deadline_requested = any(
        marker in normalized for marker in ("за 1 день", "за один день", "через день", "до завтра")
    )
    if deadline_requested and not successful.intersection(
        {"UpdateIssue", "tracker_patch_issue", "tracker_update_issue"}
    ):
        return (
            "Пользователь явно задал срок в один день. "
            "Установи deadline через UpdateIssue, затем заверши ответ."
        )
    return None


# ---------------------------------------------------------------------------
# ReActRunner
# ---------------------------------------------------------------------------


class ReActRunner:
    """
    Orchestrates a multi-turn ReAct agent loop.

    Parameters
    ----------
    agent:
        A :class:`~core.agent.BaseAgent` subclass instance.
    runtime_config:
        Autonomy rules (auto_risk / confirm_risk). Defaults to
        ``RuntimeConfig()`` which auto-executes only ``low``-risk tools.
    db_session:
        Optional SQLAlchemy ``AsyncSession``. When provided, actions / traces
        / confirms are persisted to the database. When ``None``, an in-memory
        store is used — suitable for tests and single-process scenarios.
    max_iterations:
        Hard cap on LLM calls per conversation turn to prevent runaway loops.
    team_id:
        UUID of the owning team. Required only when ``db_session`` is set.
    """

    def __init__(
        self,
        agent: BaseAgent,
        runtime_config: RuntimeConfig | None = None,
        db_session: Any | None = None,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
        team_id: str | None = None,
    ) -> None:
        self.agent = agent
        self.runtime_config = runtime_config or RuntimeConfig()
        self.db_session = db_session
        self.max_iterations = max_iterations
        self.team_id = team_id

        # In-memory fallback stores
        self._mem_sessions: dict[str, dict[str, Any]] = {}
        self._mem_confirms: dict[str, dict[str, Any]] = {}
        self._active_invocation_context: InvocationContext | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _make_ctx(
        self,
        db_session: Any | None,
        team_id: str | None,
        effective_prompt: str | None = None,
        effective_runtime_config: Any | None = None,
        invocation_context: InvocationContext | dict[str, Any] | None = None,
    ) -> _RunCtx:
        """Build the per-call context, defaulting to instance-level values."""
        return _RunCtx(
            db_session=db_session if db_session is not None else self.db_session,
            team_id=team_id if team_id is not None else self.team_id,
            effective_prompt=effective_prompt,
            effective_runtime_config=effective_runtime_config,
            invocation_context=normalize_invocation_context(invocation_context),
        )

    async def invoke(
        self,
        message: str,
        session_id: str,
        *,
        db_session: Any | None = None,
        team_id: str | None = None,
        effective_prompt: str | None = None,
        effective_runtime_config: Any | None = None,
        invocation_context: InvocationContext | dict[str, Any] | None = None,
    ) -> AgentResult:
        """Start a new turn or continue an existing session.

        Parameters
        ----------
        message:
            The user's text input.
        session_id:
            Opaque string that identifies the conversation. Re-use the same
            value across turns to maintain history.
        db_session:
            Optional per-call SQLAlchemy ``AsyncSession``. Overrides the
            instance-level session for this call. When ``None`` (and no
            instance session is set) the in-memory store is used.
        team_id:
            Owning team UUID, required for DB persistence.
        """
        ctx = self._make_ctx(
            db_session,
            team_id,
            effective_prompt,
            effective_runtime_config,
            invocation_context,
        )
        state = await self._load_session(ctx, session_id)
        if ctx.invocation_context is not None:
            state["context"] = ctx.invocation_context.model_dump(exclude_none=True)
        state["messages"].append({"role": "user", "content": message})
        state["_turn_user_message"] = message
        await self._compact_session_history(state)
        from core.backlog_context import set_pending_backlog_plan

        set_pending_backlog_plan(None)

        if not getattr(self.agent, "action_only", False):
            scenario_steps_before = len(state["steps"])
            outcome = await self._run_scenario(ctx, session_id, state, scenario_steps_before)
            return self._scenario_outcome_to_agent_result(
                ctx, session_id, state, outcome, scenario_steps_before
            )

        if getattr(self.agent, "freeform_tool_planning", False):
            # Freeform agents receive the original request and session history directly.
            # The stage is retained only as trace metadata; it does not gate tools,
            # trigger missing-info clarification, or impose a deterministic plan.
            stage = await detect_stage(message, use_llm=False)
            goal_plan = GoalPlan.single(stage, message, rationale="freeform")
            state["_plan"] = serialize_plan(goal_plan)
            state["_plan_cursor"] = 0
            state["_scenario_retries"] = {}
            return await self._execute_turn_plan(ctx, session_id, state, goal_plan, start_index=0)

        goal_plan = await build_goal_plan(message, use_llm=True)
        state["_plan"] = serialize_plan(goal_plan)
        state["_plan_cursor"] = 0
        state["_scenario_retries"] = {}
        steps_before = len(state["steps"])

        all_missing = []
        for item in goal_plan.items:
            all_missing.extend(item.missing_info)

        # Strip non-blocking optional fields — deadline/SP/priority never stop execution
        _OPTIONAL_MISSING = (
            "дата",
            "дедлайн",
            "deadline",
            "срок",
            "завершен",
            "story point",
            " sp",
            "приоритет",
            "priority",
            "описани",
            "description",
            "спринт",
            "sprint",
        )
        all_missing = [
            m for m in all_missing if not any(kw in m.lower() for kw in _OPTIONAL_MISSING)
        ]
        for item in goal_plan.items:
            item.missing_info = [
                m for m in item.missing_info if not any(kw in m.lower() for kw in _OPTIONAL_MISSING)
            ]

        # Resolve "which task" from recent session history before asking user
        _TASK_MISSING_KW = ("задач", "ключ", "issue", "какую", "задан")
        recent_user_msgs = [
            m["content"]
            for m in state["messages"][-6:]
            if m.get("role") == "user" and m.get("content") != message
        ]
        import re as _re

        _key_re = _re.compile(r"[A-Z]+-\d+")
        _hint_re = _re.compile(
            r"задач[уаеи]?\s+по\s+(\w+)|"
            r"задач[уаеи]?\s+[«\"']?(\w[\w\s]*?)[»\"']?(?:\s|,|$)",
            _re.IGNORECASE,
        )
        history_task_mentions: list[str] = []
        for msg in recent_user_msgs:
            history_task_mentions += _key_re.findall(msg)
            history_task_mentions += [m for g in _hint_re.findall(msg) for m in g if m]
        if history_task_mentions:
            for item in goal_plan.items:
                item.missing_info = [
                    m
                    for m in item.missing_info
                    if not any(kw in m.lower() for kw in _TASK_MISSING_KW)
                ]
            all_missing = [m for item in goal_plan.items for m in item.missing_info]

        # Resolve 1st-person ("мне/я/мои") from invocation context
        invocation = (
            ctx.invocation_context or self._active_invocation_context or InvocationContext()
        )
        actor_login = invocation.actor_tracker_login
        user_msg = message
        from core.assignee_resolver import resolve_first_person

        resolved_login = (
            resolve_first_person(user_msg, tracker_login=actor_login) if actor_login else None
        )

        if resolved_login:
            # Substitute 1st-person references in goal items and clear related missing_info
            for item in goal_plan.items:
                if item.entities and "assignee" in item.entities:
                    item.entities["assignee"] = resolved_login
                item.missing_info = [
                    m
                    for m in item.missing_info
                    if "исполнител" not in m.lower() and "assignee" not in m.lower()
                ]
            all_missing = [m for item in goal_plan.items for m in item.missing_info]

        # Resolve team member names ("Коля" etc) from entities
        for item in goal_plan.items:
            if not item.entities:
                continue
            mention = item.entities.get("assignee")
            if not mention:
                continue
            try:
                from core.config import get_config as _get_cfg
                from core.tracker import TrackerClient as _TrackerClient

                _cfg = _get_cfg()
                _qkey = _cfg.tracker.tracker_queue
                async with _TrackerClient() as _tc:
                    _users = await load_team_users(_tc, _qkey)
                match = best_user_match(mention, _users)
                if match and match.score >= 0.42:
                    item.entities["assignee"] = match.login
                    item.missing_info = [
                        m
                        for m in item.missing_info
                        if "исполнител" not in m.lower() and "assignee" not in m.lower()
                    ]
            except Exception:
                pass  # resolution is best-effort, don't block on failure
            all_missing = [m for item in goal_plan.items for m in item.missing_info]

        if all_missing:
            question = "Уточни, пожалуйста: " + "; ".join(all_missing)
            state["steps"].append(_step("clarification", content=question))
            return AgentResult(
                reply=question,
                session_id=session_id,
                steps=state["steps"][steps_before:],
            )

        if goal_plan.is_dialog:
            return await self._run_dialog(ctx, session_id, state)

        return await self._execute_turn_plan(ctx, session_id, state, goal_plan, start_index=0)

    async def _set_turn_stage(
        self, state: dict[str, Any], message: str, *, use_llm: bool = True
    ) -> None:
        """Classify the message into ONE stage, frozen for the whole turn.

        Rules first, LLM classifier fallback (``use_llm`` False on resume — the
        confirmed tool already passed the gate, so a safe QUERY fallback cannot
        block it, and we avoid a second classifier call).
        """
        stage_id = await detect_stage(message, use_llm=use_llm)
        state["_stage"] = stage_id.value
        stage = get_stage(stage_id)
        state["stage_addendum"] = stage.prompt_addendum if stage else ""

    def _freeze_scenario_stage(self, state: dict[str, Any], item: GoalItem) -> None:
        state["_stage"] = item.stage.value
        state["_current_goal_item"] = item
        if getattr(self.agent, "freeform_tool_planning", False):
            state["stage_addendum"] = (
                "Работай по исходному запросу пользователя и актуальной истории чата. "
                "Сам определи цель, обязательные данные и критерий успеха. "
                "Не проси уточнений, если название, описание или другие безопасные поля "
                "можно разумно сформулировать из контекста.\n"
                "Самостоятельно спланируй последовательность вызовов. После каждого результата "
                "переоцени остаток плана; не следуй фиксированному сценарию, "
                "если данные требуют другого пути."
            )
        else:
            stage = get_stage(item.stage)
            state["stage_addendum"] = stage.prompt_addendum if stage else ""
        state["_turn_user_message"] = item.payload
        state["steps"].append(_step("stage", stage=item.stage.value))

    async def _run_dialog(
        self, ctx: _RunCtx, session_id: str, state: dict[str, Any]
    ) -> AgentResult:
        """Single LLM call for DIALOG — no tools, no action_only report."""
        steps_before = len(state["steps"])
        stage = get_stage(StageId.DIALOG)
        state["steps"].append(_step("stage", stage=StageId.DIALOG.value))
        addendum = stage.prompt_addendum if stage else ""
        llm_messages = self._llm_messages(
            ctx,
            state["messages"],
            stage_addendum=addendum,
            session_summary=state.get("summary_context", ""),
        )
        llm_response, _ = await self.agent._call_with_fallback(llm_messages, [])
        reply = (llm_response.content or "").strip()
        if not reply or _is_safety_filtered(reply):
            reply = (
                "Я — PM-агент для Яндекс Трекера. "
                "Могу найти, создать или обновить задачи в DARKHORSE. Чем помочь?"
            )
        state["steps"].append(_step("final", content=reply, reason="dialog"))
        state["messages"].append({"role": "assistant", "content": reply})
        await self._compact_session_history(state)
        await self._save_session(ctx, session_id, state)
        turn_steps = state["steps"][steps_before:]
        return AgentResult(reply=reply or None, session_id=session_id, steps=list(turn_steps))

    def _clarification_result(
        self,
        session_id: str,
        state: dict[str, Any],
        question: str,
        scenario_steps_before: int,
    ) -> AgentResult:
        steps = state["steps"]
        steps.append(_step("clarification", question=question))
        state["messages"].append({"role": "assistant", "content": question})
        turn_steps = steps[scenario_steps_before:]
        return AgentResult(
            reply=question,
            clarification=question,
            session_id=session_id,
            steps=list(turn_steps),
        )

    def _scenario_outcome_to_agent_result(
        self,
        ctx: _RunCtx,
        session_id: str,
        state: dict[str, Any],
        outcome: ScenarioOutcome,
        scenario_steps_before: int,
    ) -> AgentResult:
        if outcome.kind == "needs_confirm" and outcome.agent_result is not None:
            return outcome.agent_result
        if outcome.kind == "clarification" and outcome.clarification:
            return self._clarification_result(
                session_id, state, outcome.clarification, scenario_steps_before
            )
        if outcome.agent_result is not None:
            return outcome.agent_result
        turn_steps = outcome.turn_steps or []
        reply = outcome.reply
        if reply is None and getattr(self.agent, "action_only", False):
            reply = _build_action_report(turn_steps) or "Действие выполнено."
        return AgentResult(
            reply=reply,
            session_id=session_id,
            steps=list(turn_steps),
        )

    async def _execute_turn_plan(
        self,
        ctx: _RunCtx,
        session_id: str,
        state: dict[str, Any],
        goal_plan: GoalPlan,
        *,
        start_index: int,
    ) -> AgentResult:
        outcomes: list[ScenarioOutcome] = []
        for idx in range(start_index, len(goal_plan.items)):
            item = goal_plan.items[idx]
            state["_plan_cursor"] = idx
            scenario_steps_before = len(state["steps"])
            self._freeze_scenario_stage(state, item)
            outcome = await self._run_scenario(ctx, session_id, state, scenario_steps_before)
            outcomes.append(outcome)
            if outcome.kind == "needs_confirm" and outcome.agent_result is not None:
                await self._save_session(ctx, session_id, state)
                return outcome.agent_result
            if outcome.kind == "clarification" and outcome.clarification:
                await self._save_session(ctx, session_id, state)
                return self._clarification_result(
                    session_id, state, outcome.clarification, scenario_steps_before
                )
        if getattr(self.agent, "freeform_tool_planning", False):
            return await self._finalize_freeform_turn(
                ctx, session_id, state, outcomes, start_index=start_index
            )
        return await self._reflect_and_finalize(
            ctx, session_id, state, goal_plan, outcomes, start_index=start_index
        )

    async def _finalize_freeform_turn(
        self,
        ctx: _RunCtx,
        session_id: str,
        state: dict[str, Any],
        outcomes: list[ScenarioOutcome],
        *,
        start_index: int,
    ) -> AgentResult:
        """Finish freeform execution without deterministic verdicts or retries."""
        outcome = outcomes[0] if outcomes else ScenarioOutcome(kind="done")
        turn_steps = list(outcome.turn_steps or [])
        reply = outcome.reply or _build_action_report(turn_steps)
        if not reply:
            reply = outcome.clarification or "Не удалось выполнить запрос."

        if not any(step.get("kind") == "final" for step in turn_steps):
            steps_offset = len(state["steps"]) - len(turn_steps)
            state["steps"].append(_step("final", content=reply, reason="freeform"))
            state["messages"].append({"role": "assistant", "content": reply})
            turn_steps = state["steps"][steps_offset:]

        state.pop("_plan", None)
        state.pop("_plan_cursor", None)
        state.pop("_scenario_retries", None)
        await self._compact_session_history(state)
        await self._save_session(ctx, session_id, state)
        return AgentResult(reply=reply, session_id=session_id, steps=turn_steps)

    def _build_multi_scenario_report(
        self,
        goal_plan: GoalPlan,
        outcomes: list[ScenarioOutcome],
    ) -> str:
        if len(goal_plan.items) == 1 and outcomes:
            outcome = outcomes[0]
            if outcome.kind == "done" and outcome.turn_steps is not None:
                single = _build_action_report(outcome.turn_steps)
                if single:
                    return single
        lines: list[str] = []
        for item, outcome in zip(goal_plan.items, outcomes):
            label = item.stage.value
            if outcome.kind == "done":
                report = _build_action_report(outcome.turn_steps or [])
                if report:
                    lines.append(f"✓ {label}: {report}")
                else:
                    lines.append(f"✓ {label}: выполнено")
            elif outcome.kind == "clarification":
                lines.append(f"? {label}: {outcome.clarification or 'нужно уточнение'}")
            else:
                reason = outcome.clarification or "не удалось"
                lines.append(f"❗ {label}: {reason}")
        return "\n".join(lines) if lines else "Действия не выполнены."

    async def _reflect_and_finalize(
        self,
        ctx: _RunCtx,
        session_id: str,
        state: dict[str, Any],
        goal_plan: GoalPlan,
        outcomes: list[ScenarioOutcome],
        *,
        start_index: int = 0,
    ) -> AgentResult:
        """Hybrid reflection: deterministic checks; LLM only for multi/failure."""
        from core.stage_graph import get_stage as _get_stage

        plan_slice = goal_plan.items[start_index:]
        if len(plan_slice) == 1 and outcomes:
            outcome = outcomes[0]
            turn_steps = list(outcome.turn_steps or [])
            item = plan_slice[0]
            stage = _get_stage(item.stage)
            if outcome.kind == "done":
                if stage and stage.id == StageId.QUERY and outcome.reply:
                    reply = outcome.reply
                elif _goal_terminal_for_stage(stage, item, turn_steps):
                    reply = _build_action_report(turn_steps) or "Действие выполнено."
                elif outcome.reply:
                    reply = outcome.reply
                else:
                    reply = _build_action_report(turn_steps) or "Действие выполнено."
                if not any(s.get("kind") == "final" for s in turn_steps):
                    steps_offset = len(state["steps"]) - len(turn_steps)
                    state["steps"].append(_step("final", content=reply, reason="stage_terminal"))
                    state["messages"].append({"role": "assistant", "content": reply})
                    turn_steps = state["steps"][steps_offset:]
                    await self._save_session(ctx, session_id, state)
                state.pop("_plan", None)
                state.pop("_plan_cursor", None)
                return AgentResult(reply=reply, session_id=session_id, steps=turn_steps)

        final_outcomes = list(outcomes)
        retries: dict[str, int] = state.setdefault("_scenario_retries", {})

        for offset, (item, outcome) in enumerate(zip(plan_slice, outcomes)):
            idx = start_index + offset
            stage = _get_stage(item.stage)
            turn_steps = outcome.turn_steps or []
            complete = outcome.kind == "done" and _goal_terminal_for_stage(stage, item, turn_steps)
            if complete:
                continue
            verdict = await _goal_met(item, turn_steps)
            if verdict.met:
                continue
            retry_key = str(idx)
            if retries.get(retry_key, 0) < 1 and outcome.kind in ("done", "max_iter"):
                retries[retry_key] = retries.get(retry_key, 0) + 1
                state["_plan_cursor"] = idx
                scenario_steps_before = len(state["steps"])
                self._freeze_scenario_stage(state, item)
                state["messages"].append(
                    {
                        "role": "user",
                        "content": (f"Цель не достигнута: {verdict.reason}. Попробуй иначе."),
                    }
                )
                retry_outcome = await self._run_scenario(
                    ctx, session_id, state, scenario_steps_before
                )
                final_outcomes[offset] = retry_outcome
                turn_steps = retry_outcome.turn_steps or []
                if retry_outcome.kind == "clarification":
                    await self._save_session(ctx, session_id, state)
                    return self._clarification_result(
                        session_id,
                        state,
                        retry_outcome.clarification or "",
                        scenario_steps_before,
                    )
                if retry_outcome.kind == "done" and _goal_terminal_for_stage(
                    stage, item, turn_steps
                ):
                    continue
            question = clarification_needed(
                item.stage,
                turn_steps,
                item.payload,
                reason="max_iter" if outcome.kind == "max_iter" else "blocked",
            )
            if question:
                final_outcomes[offset] = ScenarioOutcome(
                    kind="clarification",
                    turn_steps=turn_steps,
                    clarification=question,
                )

        needs_llm = len(plan_slice) > 1 or any(
            o.kind != "done"
            or (
                _get_stage(plan_slice[i].stage) is not None
                and not _goal_terminal_for_stage(
                    _get_stage(plan_slice[i].stage),
                    plan_slice[i],
                    o.turn_steps or [],
                )
            )
            for i, o in enumerate(final_outcomes)
        )

        reply = self._build_multi_scenario_report(GoalPlan(items=plan_slice), final_outcomes)
        if needs_llm and len(plan_slice) > 1:
            llm_reply = await self._reflection_llm_check(reply, plan_slice, final_outcomes)
            if llm_reply:
                reply = llm_reply

        scenario_steps_before = len(state["steps"])
        state["steps"].append(_step("final", content=reply, reason="multi_scenario"))
        state["messages"].append({"role": "assistant", "content": reply})
        await self._compact_session_history(state)
        await self._save_session(ctx, session_id, state)
        turn_steps = state["steps"][scenario_steps_before:]
        state.pop("_plan", None)
        state.pop("_plan_cursor", None)
        return AgentResult(reply=reply, session_id=session_id, steps=list(turn_steps))

    async def _reflection_llm_check(
        self,
        report: str,
        items: list[GoalItem],
        outcomes: list[ScenarioOutcome],
    ) -> str | None:
        """Optional LLM pass to sanity-check a multi-scenario report."""
        from core.llm import LLMClient, Message

        scenarios_text = "\n".join(f"- {item.stage.value}: {item.payload[:200]}" for item in items)
        client = LLMClient(
            model="google/gemini-3.1-flash-lite",
            provider="openrouter",
            temperature=0.0,
            max_tokens=256,
            max_retries=0,
        )
        try:
            resp = await client.complete(
                [
                    Message(
                        role="system",
                        content=(
                            "Проверь, честно ли отчёт PM-агента отражает выполненные сценарии. "
                            "Если всё верно — верни отчёт без изменений. "
                            "Если что-то не выполнено — добавь строку ❗ с причиной. "
                            "Только текст отчёта, без пояснений."
                        ),
                    ),
                    Message(
                        role="user",
                        content=f"Сценарии:\n{scenarios_text}\n\nОтчёт:\n{report}",
                    ),
                ]
            )
            text = (resp.content or "").strip()
            return text or None
        except Exception as exc:  # noqa: BLE001
            logger.warning("reflection LLM failed, keeping deterministic report: %s", exc)
            return None
        finally:
            await client.close()

    async def resume(
        self,
        confirm_id: str,
        approved: bool,
        *,
        db_session: Any | None = None,
        team_id: str | None = None,
        effective_prompt: str | None = None,
        effective_runtime_config: Any | None = None,
        invocation_context: InvocationContext | dict[str, Any] | None = None,
    ) -> AgentResult:
        """Continue a paused session after the user responds to a confirm.

        Parameters
        ----------
        confirm_id:
            The :attr:`PendingConfirm.confirm_id` returned by a previous
            ``invoke`` / ``resume`` call.
        approved:
            ``True`` → execute the pending tool; ``False`` → skip it.
        db_session / team_id / effective_prompt / effective_runtime_config:
            See :meth:`invoke`.
        """
        ctx = self._make_ctx(
            db_session,
            team_id,
            effective_prompt,
            effective_runtime_config,
            invocation_context,
        )
        confirm = await self._load_confirm(ctx, confirm_id)
        if confirm is None:
            raise AgentError(f"Confirm not found: {confirm_id!r}")

        action_only = getattr(self.agent, "action_only", False)
        session_id = confirm["session_id"]
        tool_name = confirm["tool_name"]
        tool_args = confirm["tool_args"]

        state = await self._load_session(ctx, session_id)
        if ctx.invocation_context is None:
            ctx.invocation_context = normalize_invocation_context(state.get("context"))
        if ctx.invocation_context is not None:
            ctx.invocation_context = ctx.invocation_context.model_copy(
                update={
                    "team_id": ctx.team_id or ctx.invocation_context.team_id,
                    "session_id": session_id,
                    "agent_name": self.agent.name,
                }
            )
            state["context"] = ctx.invocation_context.model_dump(exclude_none=True)
        self._active_invocation_context = ctx.invocation_context
        # Include resume-time steps (tool_result / confirm_rejected) in this turn's output.
        state["_steps_before_turn"] = len(state["steps"])
        # Re-hydrate the frozen stage when plan metadata was not persisted.
        if getattr(self.agent, "action_only", False) and not state.get("_stage"):
            await self._set_turn_stage(state, state.get("_turn_user_message", ""), use_llm=False)
        if state.get("_stage"):
            state["steps"].append(_step("stage", stage=state["_stage"], reason="resume"))

        if approved:
            try:
                result = await self._execute_tool(tool_name, tool_args)
                state["steps"].append(
                    _step("tool_result", tool_name=tool_name, tool_args=tool_args, result=result)
                )
                await self._update_action_status(ctx, confirm_id, "completed", result)
                feedback = _tool_result_message(tool_name, result, action_only=action_only)
            except Exception as exc:
                state["steps"].append(_step("tool_error", tool_name=tool_name, error=str(exc)))
                await self._update_action_status(ctx, confirm_id, "failed")
                feedback = _tool_error_message(tool_name, str(exc), action_only=action_only)
        else:
            state["steps"].append(_step("confirm_rejected", tool_name=tool_name))
            await self._update_action_status(ctx, confirm_id, "failed")
            feedback = _tool_rejected_message(tool_name, action_only=action_only)

        # Feed tool result back as user message so LLM can summarise for the user
        state["messages"].append({"role": "user", "content": feedback})
        await self._compact_session_history(state)

        await self._resolve_confirm(ctx, confirm_id, approved)

        if not action_only:
            outcome = await self._run_scenario(
                ctx, session_id, state, state.get("_steps_before_turn", len(state["steps"]))
            )
            return self._scenario_outcome_to_agent_result(
                ctx,
                session_id,
                state,
                outcome,
                state.get("_steps_before_turn", len(state["steps"])),
            )

        existing_plan = deserialize_plan(state.get("_plan"))
        if existing_plan and existing_plan.items:
            cursor = int(state.get("_plan_cursor", 0))
            if not state.get("_stage") and cursor < len(existing_plan.items):
                self._freeze_scenario_stage(state, existing_plan.items[cursor])
            return await self._execute_turn_plan(
                ctx, session_id, state, existing_plan, start_index=cursor
            )

        outcome = await self._run_scenario(
            ctx, session_id, state, state.get("_steps_before_turn", len(state["steps"]))
        )
        if outcome.kind == "needs_confirm" and outcome.agent_result is not None:
            return outcome.agent_result
        if outcome.kind == "clarification" and outcome.clarification:
            return self._clarification_result(
                session_id,
                state,
                outcome.clarification,
                state.get("_steps_before_turn", len(state["steps"])),
            )
        return self._scenario_outcome_to_agent_result(
            ctx,
            session_id,
            state,
            outcome,
            state.get("_steps_before_turn", len(state["steps"])),
        )

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    def _llm_messages(
        self,
        ctx: _RunCtx,
        messages: list[dict[str, Any]],
        *,
        prompt_vars: dict[str, Any] | None = None,
        stage_addendum: str = "",
        session_summary: str = "",
    ) -> list[Message]:
        """Build LLM input: effective DB prompt overrides class prompt when set.

        A focused per-stage ``stage_addendum`` is appended to the system message
        by plain concatenation (NOT ``.format`` — the prompt may contain literal
        braces that would break ``str.format``).
        """
        if ctx.effective_prompt:
            system_msg: Message | None = Message(role="system", content=ctx.effective_prompt)
        elif self.agent.prompt:
            system_msg = self.agent._build_system_message(prompt_vars)
        else:
            system_msg = None
        if system_msg is None:
            out: list[Message] = []
            if session_summary.strip():
                out.append(Message(**_session_context_message(session_summary.strip())))
            out.extend(Message(role=m["role"], content=m["content"]) for m in messages)
            return out
        if stage_addendum:
            system_msg = Message(role="system", content=f"{system_msg.content}\n\n{stage_addendum}")
        transport_block = format_transport_context_for_prompt(ctx.invocation_context)
        if transport_block:
            system_msg = Message(
                role="system", content=f"{system_msg.content}\n\n{transport_block}"
            )
        out: list[Message] = [system_msg]
        if session_summary.strip():
            out.append(Message(**_session_context_message(session_summary.strip())))
        for m in messages:
            if m.get("role") == "system":
                continue
            out.append(Message(role=m["role"], content=m["content"]))
        return out

    async def _run_scenario(
        self,
        ctx: _RunCtx,
        session_id: str,
        state: dict[str, Any],
        scenario_steps_before: int,
    ) -> ScenarioOutcome:
        messages: list[dict[str, Any]] = state["messages"]
        steps: list[dict[str, Any]] = state["steps"]
        if ctx.invocation_context is None:
            ctx.invocation_context = normalize_invocation_context(state.get("context"))
        if ctx.invocation_context is not None:
            ctx.invocation_context = ctx.invocation_context.model_copy(
                update={
                    "team_id": ctx.team_id or ctx.invocation_context.team_id,
                    "session_id": session_id,
                    "agent_name": self.agent.name,
                }
            )
            state["context"] = ctx.invocation_context.model_dump(exclude_none=True)
        self._active_invocation_context = ctx.invocation_context
        tool_schemas = self.agent._resolve_tool_schemas()
        registry = get_registry()
        action_only = getattr(self.agent, "action_only", False)
        freeform = bool(getattr(self.agent, "freeform_tool_planning", False))
        steps_before_turn = state.pop("_steps_before_turn", scenario_steps_before)
        # The stage is a prompt hint only. The agent plans its own tool cascade.
        stage = get_stage(state.get("_stage")) if action_only else None
        if stage is not None and not freeform:
            tool_schemas = [
                schema for schema in tool_schemas if schema.get("name") in stage.allowed_tools
            ] or tool_schemas

        for iteration in range(self.max_iterations):
            logger.debug(
                "ReAct iteration %d/%d session=%s agent=%s",
                iteration + 1,
                self.max_iterations,
                session_id,
                self.agent.name,
            )

            llm_messages = self._llm_messages(
                ctx,
                messages,
                prompt_vars=state.get("prompt_vars"),
                stage_addendum=state.get("stage_addendum", ""),
                session_summary=state.get("summary_context", ""),
            )
            llm_response, _ = await self.agent._call_with_fallback(llm_messages, tool_schemas)

            if not llm_response.tool_calls:
                llm_text = (llm_response.content or "").strip()
                turn_steps = steps[steps_before_turn:]
                had_tool = any(
                    s.get("kind") in ("tool_call", "tool_result", "confirm_wait")
                    for s in turn_steps
                )

                if action_only:
                    if freeform:
                        unfinished = _freeform_unfinished_action(
                            state.get("_turn_user_message", ""),
                            StageId(state["_stage"]) if state.get("_stage") else None,
                            turn_steps,
                        )
                        if unfinished:
                            messages.append({"role": "user", "content": unfinished})
                            await self._compact_session_history(state)
                            continue
                    if not freeform:
                        question = clarification_needed(
                            state.get("_stage"),
                            turn_steps,
                            state.get("_turn_user_message", ""),
                            reason="blocked",
                        )
                        if question:
                            return ScenarioOutcome(
                                kind="clarification",
                                turn_steps=list(turn_steps),
                                clarification=question,
                            )
                    reply = _action_only_final_reply(
                        turn_steps,
                        llm_text,
                        had_tool,
                        stage_id=(StageId(state["_stage"]) if state.get("_stage") else None),
                    )
                else:
                    reply = llm_text
                if action_only and state.get("_plan"):
                    turn_steps = steps[steps_before_turn:]
                    return ScenarioOutcome(kind="done", turn_steps=list(turn_steps), reply=reply)
                steps.append(_step("final", content=reply))
                messages.append({"role": "assistant", "content": reply})
                state["messages"] = messages
                state["steps"] = steps
                await self._compact_session_history(state)
                await self._save_session(ctx, session_id, state)
                turn_steps = steps[steps_before_turn:]
                return ScenarioOutcome(
                    kind="done",
                    turn_steps=list(turn_steps),
                    reply=reply,
                    agent_result=AgentResult(
                        reply=reply or None, session_id=session_id, steps=list(turn_steps)
                    ),
                )

            # --- Tool call ---
            tool_call = llm_response.tool_calls[0]
            steps.append(
                _step("tool_call", tool_name=tool_call.name, tool_args=tool_call.arguments)
            )

            if not registry.exists(tool_call.name):
                err = f"Tool '{tool_call.name}' is not registered"
                logger.warning("Agent %s: %s", self.agent.name, err)
                steps.append(
                    _step(
                        "tool_error",
                        tool_name=tool_call.name,
                        error=err,
                        status="unknown_tool",
                    )
                )
                messages.append(
                    {"role": "user", "content": f"Ошибка: {err}. Сообщи об этом пользователю."}
                )
                await self._compact_session_history(state)
                continue

            if action_only and stage is not None and not freeform:
                from core.config import get_config

                guard_err: str | None = None
                decision = stage.check_tool(
                    tool_call.name, tool_call.arguments, steps[steps_before_turn:]
                )
                if not decision.allow:
                    guard_err = decision.reason
                elif stage.id == StageId.INTAKE and tool_call.name == "tracker_create_issue":
                    if message_has_create_sprint_intent(state.get("_turn_user_message", "")):
                        guard_err = (
                            "Пользователь просит создать спринт. Используй "
                            "tracker_create_sprint(name, start_date, end_date, "
                            "board_id или board_name), а не tracker_create_issue."
                        )
                    else:
                        guard_err = await check_create_assignee(
                            tool_args=tool_call.arguments,
                            turn_user_message=state.get("_turn_user_message", ""),
                            queue_key=get_config().tracker.tracker_queue,
                        )
                if guard_err:
                    steps.append(
                        _step(
                            "tool_error",
                            tool_name=tool_call.name,
                            error=guard_err,
                            status="guard_rejected",
                        )
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": _tool_error_message(
                                tool_call.name, guard_err, action_only=True
                            ),
                        }
                    )
                    await self._compact_session_history(state)
                    continue

            tool = registry.get(tool_call.name)
            rc = ctx.effective_runtime_config or self.runtime_config
            needs_confirm = not rc.skip_tool_confirm and (
                tool.name in rc.always_confirm_tools
                or (tool.risk in rc.confirm_risk and tool.risk not in rc.auto_risk)
            )

            if needs_confirm:
                # --- Autonomy gate: pause and ask ---
                confirm_id = str(uuid.uuid4())
                confirm_prompt = (
                    f"Запрос на действие: {_TOOL_LABELS.get(tool_call.name, tool_call.name)}\n"
                    f"Риск: {tool.risk}\n"
                    f"Параметры: {tool_call.arguments}\n"
                    f"Разрешить?"
                )
                pending = PendingConfirm(
                    confirm_id=confirm_id,
                    tool_name=tool_call.name,
                    tool_args=tool_call.arguments,
                    risk=tool.risk,
                    prompt=confirm_prompt,
                )
                steps.append(_step("confirm_wait", confirm_id=confirm_id, tool_name=tool_call.name))
                state["messages"] = messages
                state["steps"] = steps
                await self._save_confirm(
                    ctx, confirm_id, session_id, tool_call.name, tool_call.arguments
                )
                await self._save_session(ctx, session_id, state)
                await self._persist_action(
                    ctx,
                    confirm_id=confirm_id,
                    session_id=session_id,
                    tool_name=tool_call.name,
                    tool_args=tool_call.arguments,
                    risk=tool.risk,
                    status="pending",
                )
                turn_steps = steps[steps_before_turn:]
                return ScenarioOutcome(
                    kind="needs_confirm",
                    turn_steps=list(turn_steps),
                    agent_result=AgentResult(
                        pending_confirm=pending,
                        session_id=session_id,
                        steps=list(turn_steps),
                    ),
                )

            turn_slice = steps[steps_before_turn:]
            if action_only and _is_duplicate_tool_success(
                turn_slice, tool_call.name, tool_call.arguments
            ):
                steps.append(
                    _step(
                        "tool_error",
                        tool_name=tool_call.name,
                        error="Уже выполнено с теми же аргументами в этом запросе",
                        status="duplicate",
                    )
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"«{tool_call.name}» уже выполнен. Заверши ход БЕЗ tool calls."
                        ),
                    }
                )
                await self._compact_session_history(state)
                if not freeform and self._turn_is_done(
                    stage,
                    steps[steps_before_turn:],
                    goal_item=state.get("_current_goal_item"),
                ):
                    turn_steps = steps[steps_before_turn:]
                    if state.get("_plan"):
                        return ScenarioOutcome(kind="done", turn_steps=list(turn_steps))
                    reply = _build_action_report(turn_steps) or "Действие выполнено."
                    steps.append(_step("final", content=reply, reason="stage_terminal"))
                    messages.append({"role": "assistant", "content": reply})
                    state["messages"] = messages
                    state["steps"] = steps
                    await self._save_session(ctx, session_id, state)
                    return ScenarioOutcome(
                        kind="done",
                        turn_steps=list(turn_steps),
                        agent_result=AgentResult(
                            reply=reply,
                            session_id=session_id,
                            steps=list(turn_steps),
                        ),
                    )
                continue

            # --- Auto-execute ---
            exec_args = dict(tool_call.arguments)
            result: Any = None
            if not freeform and tool_call.name == "tracker_apply_backlog_plan":
                from core.backlog_tools import plan_json_looks_invalid

                if plan_json_looks_invalid(str(exec_args.get("plan_json", ""))):
                    exec_args["plan_json"] = ""
            try:
                result = await self._execute_tool(tool_call.name, exec_args)
                steps.append(
                    _step(
                        "tool_result",
                        tool_name=tool_call.name,
                        tool_args=exec_args,
                        result=result,
                    )
                )
                await self._persist_action(
                    ctx,
                    confirm_id=None,
                    session_id=session_id,
                    tool_name=tool_call.name,
                    tool_args=exec_args,
                    risk=tool.risk,
                    status="completed",
                    output=result,
                )

                if not freeform and (
                    tool_call.name == "backlog_plan"
                    and isinstance(result, dict)
                    and result.get("plan")
                    and not result.get("error")
                    and (result.get("tasks_count", 0) > 0 or result.get("stories_count", 0) > 0)
                ):
                    from core.backlog_context import set_pending_backlog_plan

                    set_pending_backlog_plan(result["plan"])

                if not freeform:
                    if stage is not None:
                        await self._run_forced_edges(
                            ctx, session_id, stage, steps, steps_before_turn
                        )
                    else:
                        await self._run_legacy_backlog_chain(
                            ctx,
                            session_id,
                            state,
                            steps,
                            steps_before_turn,
                            tool_call.name,
                        )

                feedback = _tool_result_message(tool_call.name, result, action_only=action_only)
            except Exception as exc:
                err_msg = str(exc)
                steps.append(_step("tool_error", tool_name=tool_call.name, error=err_msg))
                await self._persist_action(
                    ctx,
                    confirm_id=None,
                    session_id=session_id,
                    tool_name=tool_call.name,
                    tool_args=exec_args,
                    risk=tool.risk,
                    status="failed",
                )
                feedback = _tool_error_message(tool_call.name, err_msg, action_only=action_only)

            if freeform:
                feedback += _progress_checkpoint(
                    state.get("_current_goal_item"),
                    steps[steps_before_turn:],
                    iterations_left=self.max_iterations - iteration - 1,
                )
            messages.append({"role": "user", "content": feedback})
            await self._compact_session_history(state)

            if (
                not freeform
                and action_only
                and self._turn_is_done(stage, steps[steps_before_turn:])
            ):
                turn_steps = steps[steps_before_turn:]
                if state.get("_plan"):
                    if stage is not None and stage.id == StageId.QUERY:
                        verbalize = (
                            "Данные получены. Ответь на вопрос пользователя своими словами, "
                            "без tool calls."
                        )
                        if not messages or messages[-1].get("content") != verbalize:
                            messages.append({"role": "user", "content": verbalize})
                            await self._compact_session_history(state)
                            continue
                    return ScenarioOutcome(kind="done", turn_steps=list(turn_steps))
                reply = _build_action_report(turn_steps) or "Действие выполнено."
                steps.append(_step("final", content=reply, reason="stage_terminal"))
                messages.append({"role": "assistant", "content": reply})
                state["messages"] = messages
                state["steps"] = steps
                await self._save_session(ctx, session_id, state)
                return ScenarioOutcome(
                    kind="done",
                    turn_steps=list(turn_steps),
                    agent_result=AgentResult(
                        reply=reply,
                        session_id=session_id,
                        steps=list(turn_steps),
                    ),
                )

        # Max iterations reached (safety cap)
        logger.warning(
            "max_iterations reached: session=%s agent=%s stage=%s",
            session_id,
            self.agent.name,
            state.get("_stage"),
        )
        turn_steps = steps[steps_before_turn:]
        if not freeform:
            question = clarification_needed(
                state.get("_stage"),
                turn_steps,
                state.get("_turn_user_message", ""),
                reason="max_iter",
            )
            if question:
                return ScenarioOutcome(
                    kind="clarification",
                    turn_steps=list(turn_steps),
                    clarification=question,
                )
        report = _build_action_report(turn_steps)
        reply = report or "Не удалось завершить запрос. Уточни задачу или ключ задачи."
        if state.get("_plan"):
            return ScenarioOutcome(
                kind="max_iter",
                turn_steps=list(turn_steps),
                clarification=reply,
            )
        steps.append(_step("final", content=reply, reason="max_iterations"))
        state["messages"] = messages
        state["steps"] = steps
        await self._compact_session_history(state)
        await self._save_session(ctx, session_id, state)
        turn_steps = steps[steps_before_turn:]
        return ScenarioOutcome(
            kind="max_iter",
            turn_steps=list(turn_steps),
            agent_result=AgentResult(reply=reply, session_id=session_id, steps=list(turn_steps)),
        )

    # ------------------------------------------------------------------
    # Stage graph helpers
    # ------------------------------------------------------------------

    def _turn_is_done(
        self,
        stage: Any,
        turn_steps: list[dict[str, Any]],
        *,
        goal_item: GoalItem | None = None,
    ) -> bool:
        if stage is not None:
            return _goal_terminal_for_stage(stage, goal_item, turn_steps)
        return _should_auto_finalize_turn(turn_steps)

    async def _run_forced_edges(
        self,
        ctx: _RunCtx,
        session_id: str,
        stage: Any,
        steps: list[dict[str, Any]],
        steps_before_turn: int,
    ) -> None:
        """Run deterministic forced steps (no LLM round-trip) until none remain.

        Generalizes the backlog_plan -> apply auto-chain: e.g. BOARD forces
        ``tracker_apply_backlog_plan`` after a successful plan.
        """
        registry = get_registry()
        guard = 0
        while guard < 8:
            guard += 1
            forced = stage.next_forced_step(steps[steps_before_turn:])
            if forced is None:
                return
            # Forced apply always passes an empty plan_json (the stashed plan is
            # injected by the tool), so no plan_json validation is needed here.
            exec_args = dict(forced.tool_args)
            steps.append(_step("tool_call", tool_name=forced.tool_name, tool_args=exec_args))
            try:
                forced_result = await self._execute_tool(forced.tool_name, exec_args)
                steps.append(
                    _step(
                        "tool_result",
                        tool_name=forced.tool_name,
                        tool_args=exec_args,
                        result=forced_result,
                    )
                )
                await self._persist_action(
                    ctx,
                    confirm_id=None,
                    session_id=session_id,
                    tool_name=forced.tool_name,
                    tool_args=exec_args,
                    risk=registry.get(forced.tool_name).risk,
                    status="completed",
                    output=forced_result,
                )
            except Exception as forced_exc:
                steps.append(_step("tool_error", tool_name=forced.tool_name, error=str(forced_exc)))
                return

    async def _run_legacy_backlog_chain(
        self,
        ctx: _RunCtx,
        session_id: str,
        state: dict[str, Any],
        steps: list[dict[str, Any]],
        steps_before_turn: int,
        last_tool_name: str,
    ) -> None:
        """Backlog auto-chain for non-stage action_only agents (preserved)."""
        if last_tool_name != "backlog_plan":
            return
        last_plan = None
        for s in reversed(steps[steps_before_turn:]):
            if s.get("kind") == "tool_result" and s.get("tool_name") == "backlog_plan":
                last_plan = s.get("result")
                break
        if not isinstance(last_plan, dict) or last_plan.get("error") or not last_plan.get("plan"):
            return
        if not (last_plan.get("tasks_count", 0) > 0 or last_plan.get("stories_count", 0) > 0):
            return
        from core.turn_guards import message_has_backlog_intent

        turn_msg = state.get("_turn_user_message", "")
        apply_done = any(
            s.get("kind") == "tool_result" and s.get("tool_name") == "tracker_apply_backlog_plan"
            for s in steps[steps_before_turn:]
        )
        if not (message_has_backlog_intent(turn_msg) and not apply_done):
            return
        registry = get_registry()
        apply_args = {"plan_json": ""}
        steps.append(
            _step("tool_call", tool_name="tracker_apply_backlog_plan", tool_args=apply_args)
        )
        try:
            apply_result = await self._execute_tool("tracker_apply_backlog_plan", apply_args)
            steps.append(
                _step(
                    "tool_result",
                    tool_name="tracker_apply_backlog_plan",
                    tool_args=apply_args,
                    result=apply_result,
                )
            )
            await self._persist_action(
                ctx,
                confirm_id=None,
                session_id=session_id,
                tool_name="tracker_apply_backlog_plan",
                tool_args=apply_args,
                risk=registry.get("tracker_apply_backlog_plan").risk,
                status="completed",
                output=apply_result,
            )
        except Exception as apply_exc:
            steps.append(
                _step("tool_error", tool_name="tracker_apply_backlog_plan", error=str(apply_exc))
            )

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        tool = get_registry().get(tool_name)
        validated = tool.validate_arguments(tool_args)
        token = set_current_invocation_context(getattr(self, "_active_invocation_context", None))
        try:
            result = tool.execute(**validated)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        finally:
            reset_current_invocation_context(token)

    async def _compact_session_history(self, state: dict[str, Any]) -> None:
        messages = state.get("messages")
        if not isinstance(messages, list) or len(messages) <= _SESSION_MESSAGE_WINDOW:
            return

        older_messages = list(messages[:-_SESSION_MESSAGE_WINDOW])
        recent_messages = list(messages[-_SESSION_MESSAGE_WINDOW:])
        summary = await self._summarize_session_context(
            state.get("summary_context"),
            older_messages,
        )
        state["summary_context"] = summary
        state["messages"] = recent_messages

    async def _summarize_session_context(
        self,
        existing_summary: str | None,
        older_messages: list[dict[str, Any]],
    ) -> str:
        existing = (existing_summary or "").strip()
        rendered_messages = _render_messages_for_summary(older_messages)
        if not rendered_messages:
            return existing

        user_parts: list[str] = []
        if existing:
            user_parts.append(f"Текущий summary:\n{existing}")
        user_parts.append(f"Новые сообщения для сжатия:\n{rendered_messages}")
        user_parts.append(
            "Обнови summary. Держи его коротким, полезным для следующих действий агента, "
            "без выдумок и без избыточного пересказа."
        )
        prompt = "\n\n".join(user_parts)

        client = LLMClient(
            model="google/gemini-3.1-flash-lite",
            provider="openrouter",
            temperature=0.0,
            max_tokens=1200,
            max_retries=0,
        )
        try:
            response = await client.complete(
                [
                    Message(role="system", content=_SESSION_CONTEXT_SYSTEM),
                    Message(role="user", content=prompt[:12000]),
                ]
            )
            summary = (response.content or "").strip()
            if summary:
                return summary
        except Exception as exc:  # noqa: BLE001 - compaction must never break the turn
            logger.warning("session context compaction failed, using fallback: %s", exc)
        finally:
            await client.close()

        return _fallback_session_context(existing, older_messages)

    # ------------------------------------------------------------------
    # Session state (DB or in-memory)
    # ------------------------------------------------------------------

    _PERSISTED_META_KEYS = (
        "_plan",
        "_plan_cursor",
        "_scenario_retries",
        "_stage",
        "stage_addendum",
        "_turn_user_message",
        "summary_context",
    )

    def _session_meta_slice(self, state: dict[str, Any]) -> dict[str, Any]:
        return {k: state[k] for k in self._PERSISTED_META_KEYS if k in state}

    async def _load_session(self, ctx: _RunCtx, session_id: str) -> dict[str, Any]:
        if ctx.db_session is not None:
            return await self._db_load_session(ctx, session_id)
        state = dict(self._mem_sessions.get(session_id, {"messages": [], "steps": []}))
        if "context" in state:
            state["context"] = dict(state["context"])
        if "summary_context" in state:
            state["summary_context"] = str(state["summary_context"])
        return state

    async def _save_session(self, ctx: _RunCtx, session_id: str, state: dict[str, Any]) -> None:
        if ctx.db_session is not None:
            await self._db_save_session(ctx, session_id, state)
        else:
            payload: dict[str, Any] = {
                "messages": list(state["messages"]),
                "steps": list(state["steps"]),
            }
            if state.get("context") is not None:
                payload["context"] = dict(state["context"])
            payload.update(self._session_meta_slice(state))
            self._mem_sessions[session_id] = payload

    async def _load_confirm(self, ctx: _RunCtx, confirm_id: str) -> dict[str, Any] | None:
        if ctx.db_session is not None:
            return await self._db_load_confirm(ctx, confirm_id)
        return self._mem_confirms.get(confirm_id)

    async def _save_confirm(
        self,
        ctx: _RunCtx,
        confirm_id: str,
        session_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> None:
        data = {"session_id": session_id, "tool_name": tool_name, "tool_args": tool_args}
        if ctx.db_session is not None:
            await self._db_save_confirm(ctx, confirm_id, data)
        else:
            self._mem_confirms[confirm_id] = data

    async def _resolve_confirm(self, ctx: _RunCtx, confirm_id: str, approved: bool) -> None:
        if ctx.db_session is not None:
            await self._db_resolve_confirm(ctx, confirm_id, approved)
        else:
            self._mem_confirms.pop(confirm_id, None)

    async def _persist_action(
        self,
        ctx: _RunCtx,
        *,
        confirm_id: str | None,
        session_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
        risk: str,
        status: str,
        output: Any = None,
    ) -> None:
        if ctx.db_session is None:
            return
        await self._db_persist_action(
            ctx,
            confirm_id=confirm_id,
            session_id=session_id,
            tool_name=tool_name,
            tool_args=tool_args,
            risk=risk,
            status=status,
            output=output,
        )

    async def _update_action_status(
        self, ctx: _RunCtx, confirm_id: str, status: str, output: Any = None
    ) -> None:
        if ctx.db_session is None:
            return
        await self._db_update_action_status(ctx, confirm_id, status, output)

    # ------------------------------------------------------------------
    # DB implementations
    # ------------------------------------------------------------------

    async def _db_load_session(self, ctx: _RunCtx, session_id: str) -> dict[str, Any]:
        from sqlalchemy import select

        from core.models import Trace

        sid = _session_uuid(session_id)
        stmt = select(Trace).where(Trace.session_id == sid)
        row = (await ctx.db_session.execute(stmt)).scalar_one_or_none()
        if row is None:
            trace = Trace(
                id=uuid.uuid4(),
                session_id=sid,
                steps=[],
                metadata_json={
                    "messages": [],
                    "agent_name": self.agent.name,
                    "external_session_id": session_id,
                },
            )
            ctx.db_session.add(trace)
            await ctx.db_session.flush()
            return {"messages": [], "steps": [], "_trace_id": str(trace.id)}
        meta = row.metadata_json or {}
        state = {
            "messages": list(meta.get("messages", [])),
            "steps": list(row.steps or []),
            "_trace_id": str(row.id),
        }
        if meta.get("context") is not None:
            state["context"] = dict(meta["context"])
        for key in self._PERSISTED_META_KEYS:
            if key in meta:
                state[key] = meta[key]
        return state

    async def _db_save_session(self, ctx: _RunCtx, session_id: str, state: dict[str, Any]) -> None:
        from sqlalchemy import select

        from core.models import Trace

        stmt = select(Trace).where(Trace.session_id == _session_uuid(session_id))
        row = (await ctx.db_session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            row.steps = list(state["steps"])
            metadata = {
                **(row.metadata_json or {}),
                "messages": list(state["messages"]),
                "agent_name": self.agent.name,
                "external_session_id": session_id,
            }
            if state.get("context") is not None:
                metadata["context"] = dict(state["context"])
            metadata.update(self._session_meta_slice(state))
            row.metadata_json = metadata
            await ctx.db_session.flush()

    async def _db_load_confirm(self, ctx: _RunCtx, confirm_id: str) -> dict[str, Any] | None:
        from sqlalchemy import select

        from core.models import Action, Confirm, Trace

        stmt = (
            select(Confirm, Action, Trace)
            .join(Action, Confirm.action_id == Action.id)
            .join(Trace, Action.trace_id == Trace.id)
            .where(Confirm.id == uuid.UUID(confirm_id))
        )
        row = (await ctx.db_session.execute(stmt)).one_or_none()
        if row is None:
            return None
        confirm, action, trace = row
        meta = trace.metadata_json or {}
        return {
            "session_id": meta.get("external_session_id") or str(trace.session_id),
            "tool_name": action.tool_name,
            "tool_args": dict(action.input),
        }

    async def _db_save_confirm(self, ctx: _RunCtx, confirm_id: str, data: dict[str, Any]) -> None:
        # Confirm is linked to Action; saved in _db_persist_action
        pass

    async def _db_resolve_confirm(self, ctx: _RunCtx, confirm_id: str, approved: bool) -> None:
        from datetime import timezone

        from sqlalchemy import select

        from core.models import Confirm

        stmt = select(Confirm).where(Confirm.id == uuid.UUID(confirm_id))
        row = (await ctx.db_session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            row.status = "approved" if approved else "rejected"
            row.responded_at = datetime.now(timezone.utc)
            await ctx.db_session.flush()

    async def _db_persist_action(
        self,
        ctx: _RunCtx,
        *,
        confirm_id: str | None,
        session_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
        risk: str,
        status: str,
        output: Any = None,
    ) -> None:
        from sqlalchemy import select

        from core.models import Action, AgentInstance, Confirm, Trace

        if ctx.team_id is None:
            return  # team_id required for DB persistence

        stmt = select(Trace).where(Trace.session_id == _session_uuid(session_id))
        trace = (await ctx.db_session.execute(stmt)).scalar_one_or_none()
        trace_id = trace.id if trace else None
        agent_instance = (
            await ctx.db_session.execute(
                select(AgentInstance).where(
                    AgentInstance.team_id == uuid.UUID(ctx.team_id),
                    AgentInstance.name == self.agent.name,
                )
            )
        ).scalar_one_or_none()

        action = Action(
            id=uuid.UUID(confirm_id) if confirm_id else uuid.uuid4(),
            team_id=uuid.UUID(ctx.team_id),
            agent_instance_id=agent_instance.id if agent_instance else None,
            tool_name=tool_name,
            input=dict(tool_args),
            output={"result": str(output)} if output is not None else None,
            risk_level=risk,
            status=status,
            trace_id=trace_id,
        )
        ctx.db_session.add(action)
        await ctx.db_session.flush()

        if status == "pending" and confirm_id:
            confirm_prompt = (
                f"Запрос на действие: {_TOOL_LABELS.get(tool_name, tool_name)}\n"
                f"Риск: {risk}\n"
                f"Параметры: {tool_args}\n"
                f"Разрешить?"
            )
            confirm = Confirm(
                id=uuid.UUID(confirm_id),
                action_id=action.id,
                prompt=confirm_prompt,
                status="pending",
            )
            ctx.db_session.add(confirm)
            await ctx.db_session.flush()

    async def _db_update_action_status(
        self, ctx: _RunCtx, confirm_id: str, status: str, output: Any = None
    ) -> None:
        from sqlalchemy import select

        from core.models import Action

        stmt = select(Action).where(Action.id == uuid.UUID(confirm_id))
        action = (await ctx.db_session.execute(stmt)).scalar_one_or_none()
        if action is not None:
            action.status = status
            if output is not None:
                action.output = {"result": str(output)}
            await ctx.db_session.flush()


__all__ = [
    "AgentResult",
    "PendingConfirm",
    "ReActRunner",
]
