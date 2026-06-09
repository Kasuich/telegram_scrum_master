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
    chat_title: str | None = None
    message_id: str | None = None
    thread_id: str | None = None
    actor_external_id: str | None = None
    actor_display_name: str | None = None
    actor_username: str | None = None
    actor_tracker_login: str | None = None
    actor_role: str | None = None
    actor_default_board_id: str | None = None
    actor_settings: dict[str, Any] = Field(default_factory=dict)
    reply_to_message_id: str | None = None
    is_bot_mentioned: bool | None = None
    is_reply_to_bot: bool | None = None
    raw_text_without_mention: str | None = None
    telegram_message_kind: str | None = None
    has_media: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def actor_label(ctx: InvocationContext | None = None) -> str | None:
    """Human-readable label for the message author."""
    ctx = ctx or get_current_invocation_context()
    if ctx is None:
        return None
    if ctx.actor_display_name and ctx.actor_display_name.strip():
        return ctx.actor_display_name.strip()
    if ctx.actor_username and ctx.actor_username.strip():
        return f"@{ctx.actor_username.strip().lstrip('@')}"
    return None


def format_actor_prefixed_message(
    text: str,
    ctx: InvocationContext | None = None,
) -> str:
    """Format inbound text as ``Author: message`` for agent reasoning."""
    body = (text or "").strip()
    if not body:
        return body
    label = actor_label(ctx)
    if not label:
        return body
    return f"{label}: {body}"


def format_transport_context_for_prompt(ctx: InvocationContext | None) -> str:
    """Compact transport block for the LLM system prompt."""
    if ctx is None or not ctx.channel:
        return ""

    lines = ["Transport context:"]
    lines.append(f"- channel: {ctx.channel}")
    chat_type = ctx.metadata.get("chat_type")
    if chat_type:
        lines.append(f"- chat_type: {chat_type}")
    if ctx.chat_title:
        lines.append(f"- chat_title: {ctx.chat_title}")
    if ctx.actor_display_name:
        actor_line = f"- message_author: {ctx.actor_display_name}"
        if ctx.actor_username:
            actor_line += f" (@{ctx.actor_username.lstrip('@')})"
        lines.append(actor_line)
    elif ctx.actor_username:
        lines.append(f"- message_author: @{ctx.actor_username.lstrip('@')}")
    if ctx.actor_external_id:
        lines.append(f"- actor_external_id: {ctx.actor_external_id}")
    if ctx.actor_tracker_login:
        lines.append(f"- your_tracker_login: {ctx.actor_tracker_login}")
    if ctx.actor_role:
        lines.append(f"- your_role: {ctx.actor_role}")
    if ctx.actor_default_board_id:
        lines.append(f"- your_default_board: {ctx.actor_default_board_id}")
    if ctx.actor_settings:
        for k, v in ctx.actor_settings.items():
            lines.append(f"- preference_{k}: {v}")
    _rendered_identity_keys = {
        "tracker_login",
        "role",
        "default_board_id",
    }
    if ctx.metadata:
        for key in ("tracker_login", "role", "default_board_id"):
            meta_val = ctx.metadata.get(key)
            if meta_val is not None and key in _rendered_identity_keys:
                already = {
                    "tracker_login": ctx.actor_tracker_login,
                    "role": ctx.actor_role,
                    "default_board_id": ctx.actor_default_board_id,
                }[key]
                if not already:
                    label = {
                        "tracker_login": "your_tracker_login",
                        "role": "your_role",
                        "default_board_id": "your_default_board",
                    }[key]
                    lines.append(f"- {label}: {meta_val}")
    if ctx.reply_to_message_id:
        lines.append(f"- reply_to_message_id: {ctx.reply_to_message_id}")
    if ctx.is_bot_mentioned is not None:
        lines.append(f"- is_bot_mentioned: {str(ctx.is_bot_mentioned).lower()}")
    if ctx.is_reply_to_bot is not None:
        lines.append(f"- is_reply_to_bot: {str(ctx.is_reply_to_bot).lower()}")

    return "\n".join(lines)


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
    "actor_label",
    "format_actor_prefixed_message",
    "format_transport_context_for_prompt",
    "get_current_invocation_context",
    "normalize_invocation_context",
    "reset_current_invocation_context",
    "set_current_invocation_context",
]
