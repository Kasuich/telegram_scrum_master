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
    return f"Tool '{tool_name}' returned: {result}"


def _tool_rejected_message(tool_name: str) -> str:
    return (
        f"Tool '{tool_name}' was rejected by the user. "
        "Explain what you wanted to do and ask if they'd like to proceed differently."
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

    async def invoke(self, message: str, session_id: str) -> AgentResult:
        """Start a new turn or continue an existing session.

        Parameters
        ----------
        message:
            The user's text input.
        session_id:
            Opaque string that identifies the conversation. Re-use the same
            value across turns to maintain history.
        """
        state = await self._load_session(session_id)
        state["messages"].append({"role": "user", "content": message})
        return await self._run_loop(session_id, state)

    async def resume(self, confirm_id: str, approved: bool) -> AgentResult:
        """Continue a paused session after the user responds to a confirm.

        Parameters
        ----------
        confirm_id:
            The :attr:`PendingConfirm.confirm_id` returned by a previous
            ``invoke`` / ``resume`` call.
        approved:
            ``True`` → execute the pending tool; ``False`` → skip it.
        """
        confirm = await self._load_confirm(confirm_id)
        if confirm is None:
            raise AgentError(f"Confirm not found: {confirm_id!r}")

        session_id = confirm["session_id"]
        tool_name = confirm["tool_name"]
        tool_args = confirm["tool_args"]

        state = await self._load_session(session_id)

        if approved:
            try:
                result = await self._execute_tool(tool_name, tool_args)
                state["steps"].append(
                    _step("tool_result", tool_name=tool_name, tool_args=tool_args, result=result)
                )
                await self._update_action_status(confirm_id, "completed", result)
                feedback = _tool_result_message(tool_name, result)
            except Exception as exc:
                state["steps"].append(_step("tool_error", tool_name=tool_name, error=str(exc)))
                await self._update_action_status(confirm_id, "failed")
                feedback = f"Tool '{tool_name}' failed: {exc}"
        else:
            state["steps"].append(_step("confirm_rejected", tool_name=tool_name))
            await self._update_action_status(confirm_id, "failed")
            feedback = _tool_rejected_message(tool_name)

        # Inject assistant "I called the tool" + result back into history
        state["messages"].append({"role": "assistant", "content": f"[Calling tool '{tool_name}']"})
        state["messages"].append({"role": "user", "content": feedback})

        await self._resolve_confirm(confirm_id, approved)
        return await self._run_loop(session_id, state)

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    async def _run_loop(self, session_id: str, state: dict[str, Any]) -> AgentResult:
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

            # --- LLM call ---
            llm_messages = [Message(role=m["role"], content=m["content"]) for m in messages]
            llm_response, _ = await self.agent._call_with_fallback(llm_messages, tool_schemas)

            if not llm_response.tool_calls:
                # Final text reply
                reply = llm_response.content or ""
                steps.append(_step("final", content=reply))
                messages.append({"role": "assistant", "content": reply})
                state["messages"] = messages
                state["steps"] = steps
                await self._save_session(session_id, state)
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
                    {"role": "assistant", "content": f"[Tried to use '{tool_call.name}']"}
                )
                messages.append({"role": "user", "content": f"Error: {err}"})
                continue

            tool = registry.get(tool_call.name)
            rc = self.runtime_config
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
                    confirm_id, session_id, tool_call.name, tool_call.arguments
                )
                await self._save_session(session_id, state)
                await self._persist_action(
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
                    confirm_id=None,
                    session_id=session_id,
                    tool_name=tool_call.name,
                    tool_args=tool_call.arguments,
                    risk=tool.risk,
                    status="failed",
                )
                feedback = f"Tool '{tool_call.name}' failed: {err_msg}"

            messages.append({"role": "assistant", "content": f"[Called tool '{tool_call.name}']"})
            messages.append({"role": "user", "content": feedback})

        # Max iterations reached
        reply = "Достигнут лимит итераций. Пожалуйста, переформулируйте запрос."
        steps.append(_step("final", content=reply, reason="max_iterations"))
        state["messages"] = messages
        state["steps"] = steps
        await self._save_session(session_id, state)
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

    async def _load_session(self, session_id: str) -> dict[str, Any]:
        if self.db_session is not None:
            return await self._db_load_session(session_id)
        return dict(self._mem_sessions.get(session_id, {"messages": [], "steps": []}))

    async def _save_session(self, session_id: str, state: dict[str, Any]) -> None:
        if self.db_session is not None:
            await self._db_save_session(session_id, state)
        else:
            self._mem_sessions[session_id] = {
                "messages": list(state["messages"]),
                "steps": list(state["steps"]),
            }

    async def _load_confirm(self, confirm_id: str) -> dict[str, Any] | None:
        if self.db_session is not None:
            return await self._db_load_confirm(confirm_id)
        return self._mem_confirms.get(confirm_id)

    async def _save_confirm(
        self, confirm_id: str, session_id: str, tool_name: str, tool_args: dict[str, Any]
    ) -> None:
        data = {"session_id": session_id, "tool_name": tool_name, "tool_args": tool_args}
        if self.db_session is not None:
            await self._db_save_confirm(confirm_id, data)
        else:
            self._mem_confirms[confirm_id] = data

    async def _resolve_confirm(self, confirm_id: str, approved: bool) -> None:
        if self.db_session is not None:
            await self._db_resolve_confirm(confirm_id, approved)
        else:
            self._mem_confirms.pop(confirm_id, None)

    async def _persist_action(
        self,
        *,
        confirm_id: str | None,
        session_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
        risk: str,
        status: str,
        output: Any = None,
    ) -> None:
        if self.db_session is None:
            return
        await self._db_persist_action(
            confirm_id=confirm_id,
            session_id=session_id,
            tool_name=tool_name,
            tool_args=tool_args,
            risk=risk,
            status=status,
            output=output,
        )

    async def _update_action_status(self, confirm_id: str, status: str, output: Any = None) -> None:
        if self.db_session is None:
            return
        await self._db_update_action_status(confirm_id, status, output)

    # ------------------------------------------------------------------
    # DB implementations
    # ------------------------------------------------------------------

    async def _db_load_session(self, session_id: str) -> dict[str, Any]:
        from sqlalchemy import select

        from core.models import Trace

        stmt = select(Trace).where(Trace.session_id == uuid.UUID(session_id))
        row = (await self.db_session.execute(stmt)).scalar_one_or_none()
        if row is None:
            trace = Trace(
                id=uuid.uuid4(),
                session_id=uuid.UUID(session_id),
                steps=[],
                metadata_json={"messages": []},
            )
            self.db_session.add(trace)
            await self.db_session.flush()
            return {"messages": [], "steps": [], "_trace_id": str(trace.id)}
        meta = row.metadata_json or {}
        return {
            "messages": list(meta.get("messages", [])),
            "steps": list(row.steps or []),
            "_trace_id": str(row.id),
        }

    async def _db_save_session(self, session_id: str, state: dict[str, Any]) -> None:
        from sqlalchemy import select

        from core.models import Trace

        stmt = select(Trace).where(Trace.session_id == uuid.UUID(session_id))
        row = (await self.db_session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            row.steps = list(state["steps"])
            row.metadata_json = {**(row.metadata_json or {}), "messages": list(state["messages"])}
            await self.db_session.flush()

    async def _db_load_confirm(self, confirm_id: str) -> dict[str, Any] | None:
        from sqlalchemy import select

        from core.models import Action, Confirm, Trace

        stmt = (
            select(Confirm, Action, Trace)
            .join(Action, Confirm.action_id == Action.id)
            .join(Trace, Action.trace_id == Trace.id)
            .where(Confirm.id == uuid.UUID(confirm_id))
        )
        row = (await self.db_session.execute(stmt)).one_or_none()
        if row is None:
            return None
        confirm, action, trace = row
        return {
            "session_id": str(trace.session_id),
            "tool_name": action.tool_name,
            "tool_args": dict(action.input),
        }

    async def _db_save_confirm(self, confirm_id: str, data: dict[str, Any]) -> None:
        # Confirm is linked to Action; saved in _db_persist_action
        pass

    async def _db_resolve_confirm(self, confirm_id: str, approved: bool) -> None:
        from datetime import timezone

        from sqlalchemy import select

        from core.models import Confirm

        stmt = select(Confirm).where(Confirm.id == uuid.UUID(confirm_id))
        row = (await self.db_session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            row.status = "approved" if approved else "rejected"
            row.responded_at = datetime.now(timezone.utc)
            await self.db_session.flush()

    async def _db_persist_action(
        self,
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

        if self.team_id is None:
            return  # team_id required for DB persistence

        stmt = select(Trace).where(Trace.session_id == uuid.UUID(session_id))
        trace = (await self.db_session.execute(stmt)).scalar_one_or_none()
        trace_id = trace.id if trace else None

        action = Action(
            id=uuid.UUID(confirm_id) if confirm_id else uuid.uuid4(),
            team_id=uuid.UUID(self.team_id),
            tool_name=tool_name,
            input=dict(tool_args),
            output={"result": str(output)} if output is not None else None,
            risk_level=risk,
            status=status,
            trace_id=trace_id,
        )
        self.db_session.add(action)
        await self.db_session.flush()

        if status == "pending" and confirm_id:
            confirm_prompt = f"Agent wants to call '{tool_name}' (risk={risk}) with: {tool_args}"
            confirm = Confirm(
                id=uuid.UUID(confirm_id),
                action_id=action.id,
                prompt=confirm_prompt,
                status="pending",
            )
            self.db_session.add(confirm)
            await self.db_session.flush()

    async def _db_update_action_status(
        self, confirm_id: str, status: str, output: Any = None
    ) -> None:
        from sqlalchemy import select

        from core.models import Action

        stmt = select(Action).where(Action.id == uuid.UUID(confirm_id))
        action = (await self.db_session.execute(stmt)).scalar_one_or_none()
        if action is not None:
            action.status = status
            if output is not None:
                action.output = {"result": str(output)}
            await self.db_session.flush()


__all__ = [
    "AgentResult",
    "PendingConfirm",
    "ReActRunner",
]
