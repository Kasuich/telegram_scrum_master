"""
call_agent — @platform_tool that lets an agent delegate to another agent.

Design decisions:
- Tool factory pattern: ``register_call_agent_tool(svc)`` is called once at
  startup (from rpc.py lifespan) with the OrchestratorService captured in a
  closure. Avoids circular imports and ContextVar injection.
- Call chain tracked via ``contextvars.ContextVar[tuple[str, ...]]``.
  Propagates transparently through asyncio tasks (each await inherits context).
- Sub-session: deterministic uuid5 derived from the full call path so that
  each unique delegation chain accumulates its own conversation history.
- MVP limitation: if the sub-agent returns a ``pending_confirm`` the caller
  receives an explanatory string instead of a confirm prompt.
  Full confirm-tunnelling can be added when Track D ships the Telegram UI.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from core.comment_format import (
    TRACKER_COMMENT_PREFIX,
    build_tracker_comment_summarize_message,
    extract_status_author,
)
from core.exceptions import ToolExecutionError
from core.tools import platform_tool

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

MAX_CALL_DEPTH = 3

# Current call chain: tuple of agent names from outermost to current.
# ("pm_agent",) means we're one level in; ("pm_agent", "meeting_summarizer")
# means we're two levels in.
_call_chain: ContextVar[tuple[str, ...]] = ContextVar("_call_chain", default=())

# Namespace UUID for deriving stable sub-session IDs.
_SUB_SESSION_NS = uuid.UUID("a9f0c1d2-e3b4-5678-9abc-def012345678")


def _sub_session_id(chain: tuple[str, ...], target: str) -> str:
    """Stable session_id for a delegated call (path-based, deterministic)."""
    path = "/".join((*chain, target))
    return str(uuid.uuid5(_SUB_SESSION_NS, path))


def register_call_agent_tool(svc: Any) -> None:
    """Register the ``call_agent`` tool bound to *svc*.

    Must be called once at startup after agents are discovered.
    Safe to call multiple times — the ToolRegistry will raise on duplicate
    registration, so wrap with a guard if needed.
    """

    @platform_tool(name="call_agent", risk="low", scopes=["agent:call"])
    async def call_agent(target_agent: str, message: str) -> str:
        """Delegate a task to another registered agent and return its reply.

        Args:
            target_agent: Name of the target agent (e.g. 'meeting_summarizer').
            message: Message to send to the target agent.

        Returns:
            The target agent's text reply.
        """
        chain = _call_chain.get()

        # --- Guard: max depth ---
        if len(chain) >= MAX_CALL_DEPTH:
            raise ToolExecutionError(
                f"call_agent: max delegation depth {MAX_CALL_DEPTH} reached "
                f"(chain: {' → '.join(chain)})"
            )

        # --- Guard: cycle detection ---
        if target_agent in chain:
            cycle = " → ".join((*chain, target_agent))
            raise ToolExecutionError(f"call_agent: recursive cycle detected: {cycle}")

        # --- Guard: unknown agent ---
        if target_agent not in svc._runners:
            available = ", ".join(svc._runners) or "(none)"
            raise ToolExecutionError(
                f"call_agent: agent {target_agent!r} not found. Available: {available}"
            )

        payload = message
        if target_agent == "meeting_summarizer" and not payload.strip().startswith(
            TRACKER_COMMENT_PREFIX
        ):
            if extract_status_author(payload) or chain == ("pm_agent",):
                payload = build_tracker_comment_summarize_message(payload)

        sub_session = _sub_session_id(chain, target_agent)
        token = _call_chain.set((*chain, target_agent))
        try:
            result = await svc.invoke(target_agent, payload, sub_session)
        finally:
            _call_chain.reset(token)

        if result.reply:
            return result.reply

        # pending_confirm tunnelling not implemented in MVP
        if result.pending_confirm:
            logger.warning(
                "call_agent: sub-agent %r returned pending_confirm — not supported in "
                "delegated calls (MVP). Confirm id: %s",
                target_agent,
                result.pending_confirm.confirm_id,
            )
            return (
                f"Агент «{target_agent}» запросил подтверждение действия "
                f"«{result.pending_confirm.tool_name}», но в делегированных вызовах "
                f"подтверждения не поддерживаются. Попробуй другой подход."
            )

        return ""


__all__ = ["register_call_agent_tool", "MAX_CALL_DEPTH", "_call_chain"]
