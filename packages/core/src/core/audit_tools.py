"""Board-audit tool registered via @platform_tool for the audit agent.

A single read-only tool that gathers the whole board and returns a structured
PM digest (board health, hygiene gaps, overdue/stale work, per-person
breakdown). The audit agent calls it once, then writes the report.
"""

from __future__ import annotations

from typing import Any

from core.audit import build_audit_digest, gather_board_issues
from core.config import get_config
from core.tools import platform_tool

# Placeholders an LLM might pass when it has no concrete queue.
_QUEUE_PLACEHOLDERS = frozenset({"", "default", "none", "null"})


def _effective_queue(queue: str) -> str:
    q = (queue or "").strip()
    if not q or q.lower() in _QUEUE_PLACEHOLDERS:
        return get_config().tracker.tracker_queue
    return q


@platform_tool(name="audit_board_digest", risk="low", scopes=["tracker:read"])
async def audit_board_digest(queue: str = "", window_days: int = 14) -> dict[str, Any]:
    """
    One aggregate read for a full PM audit of a board. Returns board health
    (index 0..100 with Сроки/Поток/Гигиена sub-scores), hygiene gaps
    (no deadline / no estimate / unassigned), overdue/stale/aging work with
    sample issue keys, throughput, and a per-person breakdown (load share,
    overdue, stale, lead time, oldest open task).

    Call this ONCE at the start of an audit, then base the whole report on it.
    Read-only, fully autonomous (low risk). ``queue`` defaults to the team's
    configured queue; ``window_days`` is the trailing window for throughput and
    resolved-work counts.
    """
    q = _effective_queue(queue)
    window = max(1, min(window_days, 90))
    open_issues, resolved_issues = await gather_board_issues(q, window_days=window)
    return build_audit_digest(
        open_issues, resolved_issues, queue=q, window_days=window
    )
