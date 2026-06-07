"""Invocation context shared across transport, orchestrator, and tools."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from pydantic import BaseModel, Field


class InvocationContext(BaseModel):
    """Normalized per-request transport context."""

    channel: str | None = None
    team_id: str | None = None
    session_id: str | None = None
    agent_name: str | None = None
    installation_id: str | None = None
    chat_id: str | None = None
    message_id: str | None = None
    thread_id: str | None = None
    actor_external_id: str | None = None
    actor_display_name: str | None = None
    reply_to_message_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


_invocation_ctx: ContextVar[InvocationContext | None] = ContextVar(
    "invocation_context",
    default=None,
)


def normalize_invocation_context(
    value: InvocationContext | dict[str, Any] | None,
) -> InvocationContext | None:
    """Coerce user input into ``InvocationContext``."""
    if value is None or isinstance(value, InvocationContext):
        return value
    return InvocationContext(**value)


def set_current_invocation_context(ctx: InvocationContext | None) -> Token:
    """Bind invocation context to the current async task."""
    return _invocation_ctx.set(ctx)


def reset_current_invocation_context(token: Token) -> None:
    """Restore the previous invocation context."""
    _invocation_ctx.reset(token)


def get_current_invocation_context() -> InvocationContext | None:
    """Get the current invocation context for tools and hooks."""
    return _invocation_ctx.get()


__all__ = [
    "InvocationContext",
    "get_current_invocation_context",
    "normalize_invocation_context",
    "reset_current_invocation_context",
    "set_current_invocation_context",
]
