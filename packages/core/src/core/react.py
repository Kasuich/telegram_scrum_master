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

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ITERATIONS = 8

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


def _tool_result_message(tool_name: str, result: Any) -> str:
    return (
        f"Инструмент «{tool_name}» выполнен успешно. Результат: {result}. "
        "Сообщи пользователю о результате кратко и по-русски."
    )


def _tool_rejected_message(tool_name: str) -> str:
    return (
        f"Пользователь отклонил вызов инструмента «{tool_name}». "
        "Объясни, что хотел сделать, и спроси как поступить иначе."
    )


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
                feedback = _tool_result_message(tool_name, result)
            except Exception as exc:
                state["steps"].append(_step("tool_error", tool_name=tool_name, error=str(exc)))
                await self._update_action_status(ctx, confirm_id, "failed")
                feedback = (
                    f"Инструмент «{tool_name}» завершился с ошибкой: {exc}. "
                    "Сообщи об ошибке пользователю."
                )
        else:
            state["steps"].append(_step("confirm_rejected", tool_name=tool_name))
            await self._update_action_status(ctx, confirm_id, "failed")
            feedback = _tool_rejected_message(tool_name)

        # Feed tool result back as user message so LLM can summarise for the user
        state["messages"].append({"role": "user", "content": feedback})

        await self._resolve_confirm(ctx, confirm_id, approved)
        return await self._run_loop(ctx, session_id, state)

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    async def _run_loop(self, ctx: _RunCtx, session_id: str, state: dict[str, Any]) -> AgentResult:
        messages: list[dict[str, Any]] = state["messages"]
        steps: list[dict[str, Any]] = state["steps"]
        tool_schemas = self.agent._resolve_tool_schemas()
        registry = get_registry()

        for iteration in range(self.max_iterations):
            logger.debug(
                "ReAct iteration %d/%d session=%s agent=%s",
                iteration + 1,
                self.max_iterations,
                session_id,
                self.agent.name,
            )

            # --- LLM call — prepend system prompt on every turn ---
            # The system message is NOT stored in state["messages"] so it can
            # be swapped live via ctx.effective_prompt without a restart.
            system_prompt = ctx.effective_prompt or self.agent.prompt
            system_msg = Message(role="system", content=system_prompt) if system_prompt else None
            history = [Message(role=m["role"], content=m["content"]) for m in messages]
            llm_messages = [system_msg, *history] if system_msg else history
            llm_response, _ = await self.agent._call_with_fallback(llm_messages, tool_schemas)

            if not llm_response.tool_calls:
                # Final text reply
                reply = llm_response.content or ""
                steps.append(_step("final", content=reply))
                messages.append({"role": "assistant", "content": reply})
                state["messages"] = messages
                state["steps"] = steps
                await self._save_session(ctx, session_id, state)
                return AgentResult(reply=reply, session_id=session_id, steps=list(steps))

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

            tool = registry.get(tool_call.name)
            rc = ctx.effective_runtime_config or self.runtime_config
            needs_confirm = tool.name in rc.always_confirm_tools or (
                tool.risk in rc.confirm_risk and tool.risk not in rc.auto_risk
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
                return AgentResult(
                    pending_confirm=pending, session_id=session_id, steps=list(steps)
                )

            # --- Auto-execute ---
            try:
                result = await self._execute_tool(tool_call.name, tool_call.arguments)
                steps.append(
                    _step(
                        "tool_result",
                        tool_name=tool_call.name,
                        tool_args=tool_call.arguments,
                        result=result,
                    )
                )
                await self._persist_action(
                    ctx,
                    confirm_id=None,
                    session_id=session_id,
                    tool_name=tool_call.name,
                    tool_args=tool_call.arguments,
                    risk=tool.risk,
                    status="completed",
                    output=result,
                )
                feedback = _tool_result_message(tool_call.name, result)
            except Exception as exc:
                err_msg = str(exc)
                steps.append(_step("tool_error", tool_name=tool_call.name, error=err_msg))
                await self._persist_action(
                    ctx,
                    confirm_id=None,
                    session_id=session_id,
                    tool_name=tool_call.name,
                    tool_args=tool_call.arguments,
                    risk=tool.risk,
                    status="failed",
                )
                feedback = f"Tool '{tool_call.name}' failed: {err_msg}"

            messages.append({"role": "user", "content": feedback})

        # Max iterations reached
        reply = "Достигнут лимит итераций. Пожалуйста, переформулируйте запрос."
        steps.append(_step("final", content=reply, reason="max_iterations"))
        state["messages"] = messages
        state["steps"] = steps
        await self._save_session(ctx, session_id, state)
        return AgentResult(reply=reply, session_id=session_id, steps=list(steps))

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

        from core.models import Action, Confirm, Trace

        if ctx.team_id is None:
            return  # team_id required for DB persistence

        stmt = select(Trace).where(Trace.session_id == _session_uuid(session_id))
        trace = (await ctx.db_session.execute(stmt)).scalar_one_or_none()
        trace_id = trace.id if trace else None

        action = Action(
            id=uuid.UUID(confirm_id) if confirm_id else uuid.uuid4(),
            team_id=uuid.UUID(ctx.team_id),
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
