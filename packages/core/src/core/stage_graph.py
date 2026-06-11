"""
Stage graph for the action-only PM agent.

A *stage* is a deterministic sub-machine the agent walks for one turn. Each stage
declares:
  - ``allowed_tools``   — whitelist; tools outside it are rejected.
  - ``ordered_guards``  — ordering rules enforcing required predecessors
                          (e.g. comment only after the summarizer ran).
  - ``terminal``        — predicate over the turn's steps; True => end the turn.
  - ``forced_next``     — a deterministic next tool call to run WITHOUT an LLM
                          round-trip (generalizes the backlog_plan -> apply chain).
  - ``prompt_addendum`` — a focused per-stage instruction appended to the system
                          prompt.

The stage is chosen once per turn by :mod:`core.stage_router` and frozen for the
whole turn — it is never re-derived from the raw message on each tool call (that
flip-flopping was the source of unpredictable behaviour).

This module is intentionally pure and synchronous: no LLM, no IO. The one async
concern (assignee-mismatch correction on create) lives in
:func:`core.turn_guards.check_create_assignee` and is called by the runner only
inside the INTAKE stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from core.turn_guards import (
    created_issue_keys_in_turn,
    find_succeeded_in_turn,
    summarizer_call_done_in_turn,
)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class StageId(str, Enum):
    INTAKE = "INTAKE"
    STATUS = "STATUS"
    BOARD = "BOARD"
    MEETING_SYNC = "MEETING_SYNC"
    TRANSITION = "TRANSITION"
    QUERY = "QUERY"
    REORG = "REORG"
    PROACTIVE = "PROACTIVE"
    HYGIENE = "HYGIENE"
    DIALOG = "DIALOG"


@dataclass(frozen=True)
class ToolCallSpec:
    """A deterministic forced step: run this tool with these args, no LLM."""

    tool_name: str
    tool_args: dict[str, Any]


@dataclass(frozen=True)
class GuardDecision:
    allow: bool
    reason: Optional[str] = None

    @classmethod
    def ok(cls) -> GuardDecision:
        return cls(True, None)

    @classmethod
    def reject(cls, reason: str) -> GuardDecision:
        return cls(False, reason)


# A turn-step list is ``list[dict]``. Guards/predicates receive the turn slice
# (steps since the turn began) so they can reuse turn_guards helpers with
# since_index=0.
TurnSteps = list  # list[dict[str, Any]]
TurnPredicate = Callable[[TurnSteps], bool]
# (tool_name, tool_args, turn_steps) -> GuardDecision | None  (None = rule abstains)
ToolGuardRule = Callable[[str, dict, TurnSteps], Optional[GuardDecision]]


# ---------------------------------------------------------------------------
# Shared step predicates (reuse turn_guards helpers; turn slice => since=0)
# ---------------------------------------------------------------------------


def _tool_results(turn_steps: TurnSteps) -> list[dict[str, Any]]:
    return [s for s in turn_steps if s.get("kind") == "tool_result" and s.get("tool_name")]


def comment_succeeded(turn_steps: TurnSteps) -> bool:
    for s in _tool_results(turn_steps):
        if s.get("tool_name") not in ("tracker_comment_issue", "CreateComment"):
            continue
        res = s.get("result") or {}
        if isinstance(res, dict) and not res.get("error"):
            return True
    return False


def apply_backlog_succeeded(turn_steps: TurnSteps) -> bool:
    for s in _tool_results(turn_steps):
        if s.get("tool_name") != "tracker_apply_backlog_plan":
            continue
        res = s.get("result") or {}
        if isinstance(res, dict) and not res.get("error") and res.get("created_count", 0) > 0:
            return True
    return False


def create_sprint_succeeded(turn_steps: TurnSteps) -> bool:
    for s in _tool_results(turn_steps):
        if s.get("tool_name") != "tracker_create_sprint":
            continue
        res = s.get("result") or {}
        if isinstance(res, dict) and not res.get("error") and res.get("id"):
            return True
    return False


def transition_or_close_succeeded(turn_steps: TurnSteps) -> bool:
    for s in _tool_results(turn_steps):
        if s.get("tool_name") not in (
            "tracker_transition_issue",
            "tracker_move_issues_to_in_progress",
            "tracker_close_issue",
            "tracker_close_issues",
            "ChangeIssueStatus",
            "BulkTransition",
        ):
            continue
        res = s.get("result") or {}
        if isinstance(res, dict) and not res.get("error"):
            return True
    return False


def any_read_answer(turn_steps: TurnSteps) -> bool:
    """A read stage terminates once any read tool returned (found or not)."""
    read_tools = {
        "tracker_board_snapshot",
        "tracker_find_issues",
        "tracker_search_issues",
        "tracker_get_issue",
        "tracker_list_team_members",
        "tracker_read_comments",
        "GetIssue",
        "GetIssueLinks",
        "GetIssues",
        "GetProject",
        "GetPortfolio",
        "GetGoal",
        "SearchEntities",
    }
    for s in _tool_results(turn_steps):
        if s.get("tool_name") in read_tools:
            return True
    return False


def _backlog_plan_ready(turn_steps: TurnSteps) -> bool:
    for s in _tool_results(turn_steps):
        if s.get("tool_name") != "backlog_plan":
            continue
        res = s.get("result")
        if not isinstance(res, dict):
            continue
        if res.get("error") or not res.get("plan"):
            continue
        if res.get("tasks_count", 0) > 0 or res.get("stories_count", 0) > 0:
            return True
    return False


def _apply_seen(turn_steps: TurnSteps) -> bool:
    for s in turn_steps:
        if s.get("tool_name") == "tracker_apply_backlog_plan" and s.get("kind") in (
            "tool_call",
            "tool_result",
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


@dataclass
class Stage:
    id: StageId
    allowed_tools: frozenset[str]
    terminal: TurnPredicate
    prompt_addendum: str = ""
    ordered_guards: tuple[ToolGuardRule, ...] = ()
    forced_next: Callable[[TurnSteps], Optional[ToolCallSpec]] = field(default=lambda steps: None)

    def check_tool(
        self, tool_name: str, tool_args: dict[str, Any], turn_steps: TurnSteps
    ) -> GuardDecision:
        # Ordering rules first (they may also allow a tool that is on the
        # whitelist but out of order, or reject a whitelisted-but-premature one).
        for rule in self.ordered_guards:
            decision = rule(tool_name, tool_args, turn_steps)
            if decision is not None:
                return decision
        if tool_name not in self.allowed_tools:
            return GuardDecision.reject(self._blocked_message(tool_name))
        return GuardDecision.ok()

    def is_terminal(self, turn_steps: TurnSteps) -> bool:
        return self.terminal(turn_steps)

    def next_forced_step(self, turn_steps: TurnSteps) -> Optional[ToolCallSpec]:
        return self.forced_next(turn_steps)

    def _blocked_message(self, tool_name: str) -> str:
        return _BLOCK_MESSAGES.get(
            (self.id, tool_name),
            _STAGE_BLOCK_DEFAULT.get(self.id, f"«{tool_name}» не разрешён на текущей стадии."),
        )


# ---------------------------------------------------------------------------
# Tool sets
# ---------------------------------------------------------------------------

_READ_TOOLS = frozenset(
    {
        "tracker_get_queue_meta",
        "tracker_list_team_members",
        "tracker_resolve_assignee",
        "tracker_find_issues",
        "tracker_get_issue",
        "tracker_search_issues",
        "tracker_list_transitions",
        "tracker_read_comments",
    }
)


# ---------------------------------------------------------------------------
# Per-stage ordering guards
# ---------------------------------------------------------------------------


def _status_guards() -> tuple[ToolGuardRule, ...]:
    def call_agent_rule(name: str, args: dict, steps: TurnSteps) -> Optional[GuardDecision]:
        if name != "call_agent":
            return None
        if str(args.get("target_agent", "")).strip() != "meeting_summarizer":
            return GuardDecision.reject(
                "Для оформления комментария используй "
                "call_agent(target_agent='meeting_summarizer', message=...)."
            )
        if not find_succeeded_in_turn(steps, 0):
            return GuardDecision.reject(
                "Сначала tracker_find_issues по assignee/summary_hint, "
                "затем call_agent для оформления текста комментария."
            )
        return GuardDecision.ok()

    def comment_rule(name: str, args: dict, steps: TurnSteps) -> Optional[GuardDecision]:
        if name != "tracker_comment_issue":
            return None
        if not summarizer_call_done_in_turn(steps, 0):
            return GuardDecision.reject(
                "Перед комментарием вызови call_agent(meeting_summarizer) "
                "для оформления текста статуса."
            )
        return GuardDecision.ok()

    return (call_agent_rule, comment_rule)


def _create_guards() -> tuple[ToolGuardRule, ...]:
    def block_close(name: str, args: dict, steps: TurnSteps) -> Optional[GuardDecision]:
        if name != "tracker_close_issue":
            return None
        keys = created_issue_keys_in_turn(steps, 0)
        if keys:
            return GuardDecision.reject(
                f"Запрещено закрывать задачу в том же запросе, где её создали "
                f"({', '.join(keys)}). Пользователь просил СОЗДАТЬ, не закрыть. "
                "Заверши ход отчётом о создании."
            )
        return None

    def block_second_create(name: str, args: dict, steps: TurnSteps) -> Optional[GuardDecision]:
        if name != "tracker_create_issue":
            return None
        keys = created_issue_keys_in_turn(steps, 0)
        is_subtask = bool(str(args.get("parent", "")).strip())
        if keys and not is_subtask:
            return GuardDecision.reject(
                f"Уже создана задача {keys[0]} в этом запросе. "
                "Одна задача на запрос; для подзадачи передай parent."
            )
        return None

    return (block_close, block_second_create)


# ---------------------------------------------------------------------------
# Forced edges
# ---------------------------------------------------------------------------


def _board_forced_next(turn_steps: TurnSteps) -> Optional[ToolCallSpec]:
    """After a successful backlog_plan, apply it deterministically (empty
    plan_json — the runner injects the stashed plan)."""
    if _backlog_plan_ready(turn_steps) and not _apply_seen(turn_steps):
        return ToolCallSpec("tracker_apply_backlog_plan", {"plan_json": ""})
    return None


# ---------------------------------------------------------------------------
# Block messages (substrings asserted by tests are intentional)
# ---------------------------------------------------------------------------

_BLOCK_MESSAGES: dict[tuple[StageId, str], str] = {
    (StageId.STATUS, "backlog_plan"): (
        "Статус из чата: find → call_agent(meeting_summarizer) → comment, не backlog_plan."
    ),
    (StageId.STATUS, "tracker_apply_backlog_plan"): (
        "Статус из чата: find → call_agent(meeting_summarizer) → comment, не backlog_plan."
    ),
    (StageId.STATUS, "tracker_create_issue"): (
        "Статус из чата — обновляем существующую задачу, не создаём новую."
    ),
    (StageId.BOARD, "tracker_create_issue"): (
        "Длинное саммари / оформление доски: используй "
        "backlog_plan → tracker_apply_backlog_plan, не tracker_create_issue."
    ),
    (StageId.BOARD, "tracker_close_issue"): (
        "В режиме оформления доски «tracker_close_issue» не нужен. "
        "Цепочка: backlog_plan → tracker_apply_backlog_plan."
    ),
    (StageId.BOARD, "tracker_find_issues"): (
        "В режиме оформления доски «tracker_find_issues» не нужен. "
        "Цепочка: backlog_plan → tracker_apply_backlog_plan."
    ),
    (StageId.BOARD, "call_agent"): (
        "В режиме оформления доски «call_agent» не нужен. "
        "Цепочка: backlog_plan → tracker_apply_backlog_plan."
    ),
    (StageId.QUERY, "tracker_create_issue"): (
        "Стадия QUERY — только чтение доски, мутации запрещены."
    ),
    (StageId.QUERY, "tracker_apply_backlog_plan"): (
        "Стадия QUERY — только чтение доски, мутации запрещены."
    ),
}

_STAGE_BLOCK_DEFAULT: dict[StageId, str] = {
    StageId.QUERY: "Стадия QUERY — только чтение доски, мутации запрещены.",
    StageId.STATUS: ("Статус из чата: find → call_agent(meeting_summarizer) → comment."),
    StageId.BOARD: "Оформление доски: backlog_plan → tracker_apply_backlog_plan.",
    StageId.MEETING_SYNC: (
        "Синхронизация доски по итогам встречи: по каждому пункту find → "
        "обнови существующую (comment/status/patch) ИЛИ создай новую."
    ),
    StageId.INTAKE: "Создание задачи: resolve_assignee → tracker_create_issue.",
    StageId.TRANSITION: (
        "Смена статуса: find → list_transitions → transition_issue / close_issue."
    ),
    StageId.REORG: (
        "Реорганизация: find/search → patch_issue / link_issues, без оформления доски."
    ),
    StageId.PROACTIVE: (
        "Проактивная проверка: snapshot/search → comment (low) или patch (через confirm)."
    ),
    StageId.HYGIENE: ("Гигиена доски: snapshot/search → patch / comment по чеклисту."),
}


# ---------------------------------------------------------------------------
# Prompt addenda (focused per-stage instructions)
# ---------------------------------------------------------------------------

_INTAKE_ADDENDUM = (
    "Активная стадия: INTAKE (создание задачи). Разрешено: resolve_assignee → "
    "create_issue. Если пользователь просит создать спринт, используй tracker_create_sprint "
    "(name, start_date, end_date, board_id или board_name), а не tracker_create_issue. "
    "Перед созданием проверь полноту карточки (summary одной строкой, "
    "исполнитель, приоритет, оценка, дедлайн, родительский эпик) и ЗАПОЛНИ пропуски "
    "сам из контекста — не спрашивай. В отчёте перечисли, что предположил."
)
_STATUS_ADDENDUM = (
    "Активная стадия: STATUS (статус «Имя: …»). Порядок: find_issues → "
    "(опц. patch_issue) → call_agent(meeting_summarizer) → comment_issue. "
    "Если есть блокер — подними приоритет и добавь наблюдателя. Если «готово» — "
    "list_transitions → transition/close."
)
_BOARD_ADDENDUM = (
    "Активная стадия: BOARD (оформление доски). Цепочка: backlog_plan(полный текст) → "
    "tracker_apply_backlog_plan(пустой plan_json). После apply проверь полноту карточек "
    "(оценка/владелец/дедлайн) и допиши пропуски; перечисли допущения в отчёте."
)
_MEETING_SYNC_ADDENDUM = (
    "Активная стадия: MEETING_SYNC (синхронизация доски по итогам встречи). "
    "Это НЕ оформление бэклога с нуля — обсуждали и новые, и уже существующие задачи.\n"
    "1) Сначала прочитай доску (tracker_board_snapshot или GetIssues) — что уже есть.\n"
    "2) Разбей саммари на пункты: решения, action items, статусы, риски.\n"
    "3) Для КАЖДОГО пункта реши — это про существующую задачу или новая работа:\n"
    "   - Существующая (совпадает по смыслу/названию с задачей на доске) → НЕ создавай. "
    "Обнови: ChangeIssueStatus (если изменился статус — по воркфлоу очереди), "
    "UpdateIssue (assignee/приоритет/дедлайн/SP — только если явно прозвучало), "
    "CreateComment (суть обсуждения).\n"
    "   - Новая → tracker_create_issue. При merged_duplicate дубль уже обновлён автоматически — "
    "просто отрази это в отчёте, пользователя не спрашивай (режим авто).\n"
    "4) Меняй только то, что явно прозвучало на встрече. Ничего не выдумывай.\n"
    "5) В конце отчёт: создано (ключи), обновлено (ключи + что именно), пропущено."
)
_TRANSITION_ADDENDUM = (
    "Активная стадия: TRANSITION. Порядок: find_issues → list_transitions → "
    "transition_issue или close_issue. Статусы не угадывай — бери из list_transitions."
)
_QUERY_ADDENDUM = (
    "Активная стадия: QUERY (только чтение). Разрешены лишь read-инструменты "
    "(board_snapshot, find/search/get, list_team_members). Любые мутации запрещены. "
    "Получи данные и оформи ответ."
)
_REORG_ADDENDUM = (
    "Активная стадия: REORG. Сначала find/search, затем patch_issue / link_issues "
    "(re-parent, reassign, приоритет, спринт). Не оформляй доску с нуля."
)
_PROACTIVE_ADDENDUM = (
    "Активная стадия: PROACTIVE (cron, без человека). Опирайся на board_snapshot и "
    "search; комментарии (low) автономны; patch/priority уйдут на подтверждение. "
    "Итог — дайджест."
)
_HYGIENE_ADDENDUM = (
    "Активная стадия: HYGIENE. board_snapshot → найди карточки без оценки/владельца/"
    "дедлайна, несогласованные приоритеты, дубли → patch/comment. Итог — отчёт о наведении порядка."
)
_DIALOG_ADDENDUM = (
    "Активная стадия: DIALOG. Обычный разговор или вопрос о боте. "
    "Ответь естественно, дружелюбно, по-русски. Никаких инструментов. "
    "Можно задать встречный вопрос."
)


# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------

INTAKE = Stage(
    id=StageId.INTAKE,
    allowed_tools=_READ_TOOLS
    | {"tracker_create_issue", "tracker_create_sprint", "tracker_link_issues"},
    terminal=lambda steps: (
        bool(created_issue_keys_in_turn(steps, 0)) or create_sprint_succeeded(steps)
    ),
    prompt_addendum=_INTAKE_ADDENDUM,
    ordered_guards=_create_guards(),
)

STATUS = Stage(
    id=StageId.STATUS,
    allowed_tools=_READ_TOOLS
    | {
        "tracker_patch_issue",
        "tracker_update_issue",
        "tracker_update_followers",
        "tracker_transition_issue",
        "tracker_close_issue",
        "call_agent",
        "tracker_comment_issue",
    },
    terminal=comment_succeeded,
    prompt_addendum=_STATUS_ADDENDUM,
    ordered_guards=_status_guards(),
)

BOARD = Stage(
    id=StageId.BOARD,
    allowed_tools=frozenset(
        {
            "backlog_plan",
            "tracker_apply_backlog_plan",
            "tracker_get_queue_meta",
            "tracker_patch_issue",
        }
    ),
    terminal=apply_backlog_succeeded,
    prompt_addendum=_BOARD_ADDENDUM,
    forced_next=_board_forced_next,
)

MEETING_SYNC = Stage(
    id=StageId.MEETING_SYNC,
    allowed_tools=_READ_TOOLS
    | {
        "tracker_board_snapshot",
        "tracker_create_issue",
        "tracker_patch_issue",
        "tracker_update_issue",
        "tracker_transition_issue",
        "tracker_comment_issue",
    },
    # Goal-based terminal (как QUERY): freeform-агент завершает ход сам, когда
    # все пункты обработаны. Стадия для pm_agent не гейтит тулзы — несёт addendum.
    terminal=lambda steps: False,
    prompt_addendum=_MEETING_SYNC_ADDENDUM,
)

TRANSITION = Stage(
    id=StageId.TRANSITION,
    allowed_tools=_READ_TOOLS
    | {
        "tracker_transition_issue",
        "tracker_move_issues_to_in_progress",
        "tracker_close_issue",
        "tracker_close_issues",
    },
    terminal=transition_or_close_succeeded,
    prompt_addendum=_TRANSITION_ADDENDUM,
)

QUERY = Stage(
    id=StageId.QUERY,
    allowed_tools=_READ_TOOLS | {"tracker_board_snapshot", "call_agent"},
    terminal=lambda steps: False,  # Goal-based terminal — react.py decides
    prompt_addendum=_QUERY_ADDENDUM,
)

REORG = Stage(
    id=StageId.REORG,
    allowed_tools=_READ_TOOLS
    | {
        "tracker_board_snapshot",
        "tracker_patch_issue",
        "tracker_update_issue",
        "tracker_link_issues",
        "tracker_add_issues_to_sprint",
        "tracker_create_issue",
        "tracker_comment_issue",
        "tracker_close_issue",
    },
    terminal=lambda steps: False,  # bulk edits: ends on max_iterations or no further tool
    prompt_addendum=_REORG_ADDENDUM,
)

PROACTIVE = Stage(
    id=StageId.PROACTIVE,
    allowed_tools=_READ_TOOLS
    | {
        "tracker_board_snapshot",
        "tracker_comment_issue",
        "tracker_patch_issue",
        "call_agent",
    },
    terminal=lambda steps: False,
    prompt_addendum=_PROACTIVE_ADDENDUM,
)

HYGIENE = Stage(
    id=StageId.HYGIENE,
    allowed_tools=_READ_TOOLS
    | {
        "tracker_board_snapshot",
        "tracker_patch_issue",
        "tracker_comment_issue",
    },
    terminal=lambda steps: False,
    prompt_addendum=_HYGIENE_ADDENDUM,
)

DIALOG = Stage(
    id=StageId.DIALOG,
    allowed_tools=frozenset(),
    terminal=lambda steps: True,
    prompt_addendum=_DIALOG_ADDENDUM,
)

STAGES: dict[StageId, Stage] = {
    s.id: s
    for s in (
        INTAKE,
        STATUS,
        BOARD,
        MEETING_SYNC,
        TRANSITION,
        QUERY,
        REORG,
        PROACTIVE,
        HYGIENE,
        DIALOG,
    )
}


def get_stage(stage_id: StageId | str | None) -> Optional[Stage]:
    if stage_id is None:
        return None
    if isinstance(stage_id, StageId):
        return STAGES.get(stage_id)
    try:
        return STAGES.get(StageId(stage_id))
    except ValueError:
        return None


__all__ = [
    "StageId",
    "Stage",
    "GuardDecision",
    "ToolCallSpec",
    "STAGES",
    "get_stage",
    "comment_succeeded",
    "apply_backlog_succeeded",
    "transition_or_close_succeeded",
    "any_read_answer",
    "create_sprint_succeeded",
]
