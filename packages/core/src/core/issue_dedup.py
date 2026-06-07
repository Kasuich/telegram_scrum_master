"""Duplicate detection before creating Tracker issues."""

from __future__ import annotations

import difflib
import os
import re
from typing import Any

from core.config import get_config
from core.tracker import TrackerClient, TrackerError
from core.tracker_tool_helpers import combine_yql, yql_quote

_CANCELLED_STATUS_KEYS = frozenset({"cancelled", "canceled"})
_DEFAULT_CANCELLED_STATUS_NAMES: tuple[str, ...] = (
    "Cancelled",
    "Canceled",
    "Отменена",
    "Отменён",
    "Отменен",
    "Отменено",
)


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е").strip()


def cancelled_status_names() -> tuple[str, ...]:
    raw = os.getenv("TRACKER_CANCELLED_STATUSES", "").strip()
    if raw:
        return tuple(s.strip() for s in raw.replace(";", ",").split(",") if s.strip())
    return _DEFAULT_CANCELLED_STATUS_NAMES


def build_dedup_status_exclusions() -> list[str]:
    """YQL clauses excluding cancelled issues only (closed tasks remain visible)."""
    return [f'Status: !"{name}"' for name in cancelled_status_names()]


def is_cancelled_issue(issue: dict[str, Any]) -> bool:
    st = issue.get("status") or {}
    key = (st.get("key") or "").lower()
    display = _norm(st.get("display") or "")
    if key in _CANCELLED_STATUS_KEYS:
        return True
    return display in {_norm(n) for n in cancelled_status_names()}


def filter_out_cancelled(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [i for i in issues if not is_cancelled_issue(i)]


def normalize_summary(text: str) -> str:
    return re.sub(r"\s+", " ", _norm(text)).strip()


def dedup_similarity_threshold() -> float:
    return get_config().backlog.dedup_similarity


def dedup_enabled_for_backlog() -> bool:
    return get_config().backlog.dedup_enabled


def dedup_enabled_for_create() -> bool:
    return get_config().tracker.tracker_dedup_enabled


def _issue_type_key(issue: dict[str, Any]) -> str:
    return str((issue.get("type") or {}).get("key") or "").lower()


def _issue_parent_key(issue: dict[str, Any]) -> str | None:
    parent = issue.get("parent")
    if isinstance(parent, dict):
        key = str(parent.get("key") or "").strip()
        return key or None
    return None


def summaries_match(planned_summary: str, candidate_summary: str, *, threshold: float) -> bool:
    a = normalize_summary(planned_summary)
    b = normalize_summary(candidate_summary)
    if not a or not b:
        return False
    if a == b:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold


def issues_match_duplicate(
    planned_summary: str,
    candidate: dict[str, Any],
    *,
    type_key: str,
    parent_key: str | None,
    threshold: float | None = None,
) -> bool:
    if is_cancelled_issue(candidate):
        return False
    if not summaries_match(
        planned_summary,
        str(candidate.get("summary") or ""),
        threshold=threshold if threshold is not None else dedup_similarity_threshold(),
    ):
        return False
    cand_type = _issue_type_key(candidate)
    req_type = type_key.strip().lower()
    if req_type and cand_type and cand_type != req_type:
        return False
    cand_parent = _issue_parent_key(candidate)
    if parent_key:
        return cand_parent == parent_key
    return cand_parent is None


def build_dedup_find_queries(
    *,
    summary: str,
    issue_type: str = "",
) -> list[str]:
    """YQL variants for duplicate search (all statuses except cancelled)."""
    queries: list[str] = []
    seen: set[str] = set()
    exclusions = combine_yql(*build_dedup_status_exclusions())
    hint = summary.strip()
    type_part = f'Type: "{issue_type.strip()}"' if issue_type.strip() else ""

    def add(q: str) -> None:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)

    if hint:
        quoted = yql_quote(hint)
        add(combine_yql(f"Summary: {quoted}", type_part, exclusions))
        for word in hint.split():
            cleaned = word.strip(".,;:!?()[]«»\"'")
            if len(cleaned) < 3:
                continue
            add(combine_yql(f"Summary: {yql_quote(cleaned)}", type_part, exclusions))

    if not queries:
        add(combine_yql(type_part, exclusions, "Sort: Updated DESC"))
    return queries


async def find_duplicate_issue(
    client: TrackerClient,
    queue: str,
    *,
    summary: str,
    issue_type: str = "",
    parent_key: str | None = None,
    threshold: float | None = None,
) -> dict[str, Any] | None:
    """
    Find an existing non-cancelled issue matching summary, type, and parent.

    Searches closed and open issues; cancelled issues are ignored.
    """
    if not summary.strip():
        return None

    thresh = threshold if threshold is not None else dedup_similarity_threshold()
    best: dict[str, Any] | None = None
    best_score = 0.0
    planned_norm = normalize_summary(summary)

    for yql in build_dedup_find_queries(summary=summary, issue_type=issue_type):
        try:
            issues = await client.search_issues(yql, queue=queue, limit=10)
        except TrackerError:
            continue
        for issue in filter_out_cancelled(issues):
            if not issues_match_duplicate(
                summary,
                issue,
                type_key=issue_type,
                parent_key=parent_key,
                threshold=thresh,
            ):
                continue
            cand_norm = normalize_summary(str(issue.get("summary") or ""))
            score = 1.0 if cand_norm == planned_norm else difflib.SequenceMatcher(
                None, planned_norm, cand_norm
            ).ratio()
            if score > best_score:
                best_score = score
                best = issue
        if best is not None:
            break

    return best


__all__ = [
    "build_dedup_status_exclusions",
    "cancelled_status_names",
    "dedup_enabled_for_backlog",
    "dedup_enabled_for_create",
    "filter_out_cancelled",
    "find_duplicate_issue",
    "is_cancelled_issue",
    "issues_match_duplicate",
    "normalize_summary",
    "summaries_match",
]
