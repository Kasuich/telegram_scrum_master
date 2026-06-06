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
from core.config import RuntimeConfig
from core.exceptions import AgentError
from core.llm import Message
from core.tools import get_registry
from core.turn_guards import check_turn_tool_guard, created_issue_keys_in_turn

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ITERATIONS = 12

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
    pending_confirm: PendingConfirm | None = None
    session_id: str
    steps: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _step(kind: str, **kwargs: Any) -> dict[str, Any]:
    return {"kind": kind, "ts": _now(), **kwargs}


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


def _format_action_tool_line(tool_name: str, result: dict[str, Any]) -> str:
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
    if tool_name == "tracker_update_followers":
        key = result.get("key") or result.get("issue_key", "")
        return f"Наблюдатели обновлены: {key}"
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
        err_n = result.get("error_count", 0)
        if n == 0 and err_n == 0:
            return (
                "Доска: не создано ни одной задачи, план пуст или backlog_plan завершился с ошибкой"
            )
        line = f"Доска: создано {n} задач"
        if epic:
            line += f", эпик {epic}"
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
        return created[-1]
    updated = [ln for ln in lines if ln.startswith(("Обновлена", "Наблюдатели"))]
    comments = [ln for ln in lines if ln.startswith("Комментарий")]
    if updated and comments:
        return f"{updated[-1]}. {comments[-1]}"
    if updated:
        return updated[-1]
    if comments:
        return comments[-1]
    for line in reversed(lines):
        if line.startswith(("Найдено:", "Закрыта")):
            return line
    if errors:
        return errors[-1]
    for line in reversed(lines):
        if line == "Задача не найдена":
            return line
    return lines[-1]


def _is_chatty_delegation(text: str) -> bool:
    """Detect LLM asking user for input instead of using tools."""
    if not text:
        return False
    lower = text.lower()
    markers = (
        "нужен ключ",
        "укажите ключ",
        "какую задачу",
        "которую вы хотите",
        "для продолжения мне нужен",
        "пожалуйста, укаж",
        "уточните",
        "подтвердите, хотите",
    )
    return "?" in text or any(m in lower for m in markers)


def _action_only_final_reply(steps: list[dict[str, Any]], llm_text: str, had_tool: bool) -> str:
    report = _build_action_report(steps)
    if report:
        return report
    if _is_chatty_delegation(llm_text):
        return (
            "Действия не выполнены: сначала tracker_find_issues "
            "(summary_hint, assignee) по контексту запроса."
        )
    if not had_tool:
        return "Действия не выполнены."
    return llm_text


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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _make_ctx(
        self,
        db_session: Any | None,
        team_id: str | None,
        effective_prompt: str | None = None,
        effective_runtime_config: Any | None = None,
    ) -> _RunCtx:
        """Build the per-call context, defaulting to instance-level values."""
        return _RunCtx(
            db_session=db_session if db_session is not None else self.db_session,
            team_id=team_id if team_id is not None else self.team_id,
            effective_prompt=effective_prompt,
            effective_runtime_config=effective_runtime_config,
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
        ctx = self._make_ctx(db_session, team_id, effective_prompt, effective_runtime_config)
        state = await self._load_session(ctx, session_id)
        state["messages"].append({"role": "user", "content": message})
        state["_turn_user_message"] = message
        state["_action_only_nudges"] = 0
        from core.backlog_context import set_pending_backlog_plan

        set_pending_backlog_plan(None)
        return await self._run_loop(ctx, session_id, state)

    async def resume(
        self,
        confirm_id: str,
        approved: bool,
        *,
        db_session: Any | None = None,
        team_id: str | None = None,
        effective_prompt: str | None = None,
        effective_runtime_config: Any | None = None,
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
        ctx = self._make_ctx(db_session, team_id, effective_prompt, effective_runtime_config)
        confirm = await self._load_confirm(ctx, confirm_id)
        if confirm is None:
            raise AgentError(f"Confirm not found: {confirm_id!r}")

        action_only = getattr(self.agent, "action_only", False)
        session_id = confirm["session_id"]
        tool_name = confirm["tool_name"]
        tool_args = confirm["tool_args"]

        state = await self._load_session(ctx, session_id)

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

        await self._resolve_confirm(ctx, confirm_id, approved)
        return await self._run_loop(ctx, session_id, state)

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    def _llm_messages(
        self,
        ctx: _RunCtx,
        messages: list[dict[str, Any]],
        *,
        prompt_vars: dict[str, Any] | None = None,
    ) -> list[Message]:
        """Build LLM input: effective DB prompt overrides class prompt when set."""
        if ctx.effective_prompt:
            system_msg: Message | None = Message(role="system", content=ctx.effective_prompt)
        elif self.agent.prompt:
            system_msg = self.agent._build_system_message(prompt_vars)
        else:
            system_msg = None
        if system_msg is None:
            return [Message(role=m["role"], content=m["content"]) for m in messages]
        out: list[Message] = [system_msg]
        for m in messages:
            if m.get("role") == "system":
                continue
            out.append(Message(role=m["role"], content=m["content"]))
        return out

    async def _run_loop(self, ctx: _RunCtx, session_id: str, state: dict[str, Any]) -> AgentResult:
        messages: list[dict[str, Any]] = state["messages"]
        steps: list[dict[str, Any]] = state["steps"]
        tool_schemas = self.agent._resolve_tool_schemas()
        registry = get_registry()
        action_only = getattr(self.agent, "action_only", False)
        steps_before_turn = len(steps)

        for iteration in range(self.max_iterations):
            logger.debug(
                "ReAct iteration %d/%d session=%s agent=%s",
                iteration + 1,
                self.max_iterations,
                session_id,
                self.agent.name,
            )

            llm_messages = self._llm_messages(ctx, messages, prompt_vars=state.get("prompt_vars"))
            llm_response, _ = await self.agent._call_with_fallback(llm_messages, tool_schemas)

            if not llm_response.tool_calls:
                llm_text = (llm_response.content or "").strip()
                turn_steps = steps[steps_before_turn:]
                had_tool = any(
                    s.get("kind") in ("tool_call", "tool_result", "confirm_wait")
                    for s in turn_steps
                )

                if action_only and not had_tool and state.get("_action_only_nudges", 0) < 4:
                    state["_action_only_nudges"] = state.get("_action_only_nudges", 0) + 1
                    if llm_text:
                        messages.append({"role": "assistant", "content": llm_text})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Запрещено спрашивать у пользователя. "
                                "Если просят СОЗДАТЬ задачу "
                                "(создай/заведи/поставь) — tracker_create_issue "
                                "(summary, assignee), без поиска. "
                                "Если закрыть/изменить/найти — "
                                "tracker_find_issues, затем действие. "
                                "Пустой поиск — только для изменения: «задача не найдена»."
                            ),
                        }
                    )
                    continue

                if action_only:
                    reply = _action_only_final_reply(turn_steps, llm_text, had_tool)
                else:
                    reply = llm_text
                steps.append(_step("final", content=reply))
                messages.append({"role": "assistant", "content": reply})
                state["messages"] = messages
                state["steps"] = steps
                await self._save_session(ctx, session_id, state)
                turn_steps = steps[steps_before_turn:]
                return AgentResult(
                    reply=reply or None, session_id=session_id, steps=list(turn_steps)
                )

            # --- Tool call ---
            tool_call = llm_response.tool_calls[0]
            steps.append(
                _step("tool_call", tool_name=tool_call.name, tool_args=tool_call.arguments)
            )

            if not registry.exists(tool_call.name):
                err = f"Tool '{tool_call.name}' is not registered"
                logger.warning("Agent %s: %s", self.agent.name, err)
                steps.append(_step("tool_error", tool_name=tool_call.name, error=err))
                messages.append(
                    {"role": "user", "content": f"Ошибка: {err}. Сообщи об этом пользователю."}
                )
                continue

            if action_only:
                from core.config import get_config

                guard_err = await check_turn_tool_guard(
                    tool_name=tool_call.name,
                    tool_args=tool_call.arguments,
                    turn_user_message=state.get("_turn_user_message", ""),
                    steps=steps,
                    steps_before_turn=steps_before_turn,
                    queue_key=get_config().tracker.tracker_queue,
                )
                if guard_err:
                    steps.append(_step("tool_error", tool_name=tool_call.name, error=guard_err))
                    messages.append(
                        {
                            "role": "user",
                            "content": _tool_error_message(
                                tool_call.name, guard_err, action_only=True
                            ),
                        }
                    )
                    if created_issue_keys_in_turn(steps, steps_before_turn):
                        last_create: dict[str, Any] | None = None
                        for s in reversed(steps):
                            if (
                                s.get("kind") == "tool_result"
                                and s.get("tool_name") == "tracker_create_issue"
                            ):
                                last_create = s.get("result")
                                break
                        if last_create:
                            reply = _format_action_tool_line("tracker_create_issue", last_create)
                            steps.append(_step("final", content=reply))
                            messages.append({"role": "assistant", "content": reply})
                            state["messages"] = messages
                            state["steps"] = steps
                            await self._save_session(ctx, session_id, state)
                            turn_steps = steps[steps_before_turn:]
                            return AgentResult(
                                reply=reply,
                                session_id=session_id,
                                steps=list(turn_steps),
                            )
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
                    f"Agent wants to call '{tool_call.name}' "
                    f"(risk={tool.risk}) with: {tool_call.arguments}"
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
                return AgentResult(
                    pending_confirm=pending, session_id=session_id, steps=list(turn_steps)
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
                if _should_auto_finalize_turn(steps[steps_before_turn:]):
                    reply = _build_action_report(steps[steps_before_turn:]) or (
                        "Действие выполнено."
                    )
                    steps.append(_step("final", content=reply))
                    messages.append({"role": "assistant", "content": reply})
                    state["messages"] = messages
                    state["steps"] = steps
                    await self._save_session(ctx, session_id, state)
                    return AgentResult(
                        reply=reply, session_id=session_id, steps=list(steps[steps_before_turn:])
                    )
                continue

            # --- Auto-execute ---
            exec_args = dict(tool_call.arguments)
            if tool_call.name == "tracker_apply_backlog_plan":
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

                if (
                    tool_call.name == "backlog_plan"
                    and isinstance(result, dict)
                    and result.get("plan")
                    and not result.get("error")
                    and (result.get("tasks_count", 0) > 0 or result.get("stories_count", 0) > 0)
                ):
                    from core.backlog_context import set_pending_backlog_plan
                    from core.turn_guards import message_has_backlog_intent

                    set_pending_backlog_plan(result["plan"])
                    turn_msg = state.get("_turn_user_message", "")
                    apply_done = any(
                        s.get("kind") == "tool_result"
                        and s.get("tool_name") == "tracker_apply_backlog_plan"
                        for s in steps[steps_before_turn:]
                    )
                    if message_has_backlog_intent(turn_msg) and not apply_done:
                        apply_args = {"plan_json": ""}
                        apply_tool = registry.get("tracker_apply_backlog_plan")
                        steps.append(
                            _step(
                                "tool_call",
                                tool_name="tracker_apply_backlog_plan",
                                tool_args=apply_args,
                            )
                        )
                        try:
                            apply_result = await self._execute_tool(
                                "tracker_apply_backlog_plan", apply_args
                            )
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
                                risk=apply_tool.risk,
                                status="completed",
                                output=apply_result,
                            )
                        except Exception as apply_exc:
                            steps.append(
                                _step(
                                    "tool_error",
                                    tool_name="tracker_apply_backlog_plan",
                                    error=str(apply_exc),
                                )
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

            messages.append({"role": "user", "content": feedback})

            if action_only and _should_auto_finalize_turn(steps[steps_before_turn:]):
                reply = _build_action_report(steps[steps_before_turn:]) or ("Действие выполнено.")
                steps.append(_step("final", content=reply))
                messages.append({"role": "assistant", "content": reply})
                state["messages"] = messages
                state["steps"] = steps
                await self._save_session(ctx, session_id, state)
                return AgentResult(
                    reply=reply, session_id=session_id, steps=list(steps[steps_before_turn:])
                )

        # Max iterations reached
        turn_steps = steps[steps_before_turn:]
        report = _build_action_report(turn_steps)
        reply = report or "Достигнут лимит итераций. Пожалуйста, переформулируйте запрос."
        steps.append(_step("final", content=reply, reason="max_iterations"))
        state["messages"] = messages
        state["steps"] = steps
        await self._save_session(ctx, session_id, state)
        turn_steps = steps[steps_before_turn:]
        return AgentResult(reply=reply, session_id=session_id, steps=list(turn_steps))

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        tool = get_registry().get(tool_name)
        validated = tool.validate_arguments(tool_args)
        result = tool.execute(**validated)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    # ------------------------------------------------------------------
    # Session state (DB or in-memory)
    # ------------------------------------------------------------------

    async def _load_session(self, ctx: _RunCtx, session_id: str) -> dict[str, Any]:
        if ctx.db_session is not None:
            return await self._db_load_session(ctx, session_id)
        return dict(self._mem_sessions.get(session_id, {"messages": [], "steps": []}))

    async def _save_session(self, ctx: _RunCtx, session_id: str, state: dict[str, Any]) -> None:
        if ctx.db_session is not None:
            await self._db_save_session(ctx, session_id, state)
        else:
            self._mem_sessions[session_id] = {
                "messages": list(state["messages"]),
                "steps": list(state["steps"]),
            }

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
                metadata_json={"messages": []},
            )
            ctx.db_session.add(trace)
            await ctx.db_session.flush()
            return {"messages": [], "steps": [], "_trace_id": str(trace.id)}
        meta = row.metadata_json or {}
        return {
            "messages": list(meta.get("messages", [])),
            "steps": list(row.steps or []),
            "_trace_id": str(row.id),
        }

    async def _db_save_session(self, ctx: _RunCtx, session_id: str, state: dict[str, Any]) -> None:
        from sqlalchemy import select

        from core.models import Trace

        stmt = select(Trace).where(Trace.session_id == _session_uuid(session_id))
        row = (await ctx.db_session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            row.steps = list(state["steps"])
            row.metadata_json = {**(row.metadata_json or {}), "messages": list(state["messages"])}
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
        return {
            "session_id": str(trace.session_id),
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
            confirm_prompt = f"Agent wants to call '{tool_name}' (risk={risk}) with: {tool_args}"
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
