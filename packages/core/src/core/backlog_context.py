"""Per-turn backlog plan stash (avoids LLM re-serializing large JSON)."""

from __future__ import annotations

import contextvars
from typing import Any

_pending_backlog_plan: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "pending_backlog_plan", default=None
)


def set_pending_backlog_plan(plan: dict[str, Any] | None) -> None:
    _pending_backlog_plan.set(plan)


def get_pending_backlog_plan() -> dict[str, Any] | None:
    return _pending_backlog_plan.get()


__all__ = ["set_pending_backlog_plan", "get_pending_backlog_plan"]
