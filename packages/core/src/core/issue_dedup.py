"""Duplicate detection before creating Tracker issues."""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

from core.config import get_config
from core.tracker import TrackerClient, TrackerError
from core.tracker_tool_helpers import combine_yql, issue_summary, yql_quote

logger = logging.getLogger(__name__)

_CANCELLED_STATUS_KEYS = frozenset({"cancelled", "canceled"})
_CLOSED_STATUS_KEYS = frozenset({"closed", "done", "resolved"})
_DEFAULT_CANCELLED_STATUS_NAMES: tuple[str, ...] = (
    "Cancelled",
    "Canceled",
    "Отменена",
    "Отменён",
    "Отменен",
    "Отменено",
)

_QUEUE_ISSUES_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_QUEUE_CACHE_TTL_SEC = 120.0
_LLM_BATCH_SIZE = 100

_DEDUP_BATCH_LLM_SYSTEM = """You match PLANNED new Yandex Tracker issues against EXISTING issues.

PLANNED items are about to be created (from a meeting or backlog). EXISTING is one batch
from the same queue (open and closed; cancelled are already excluded).

Return strict JSON only:
{
  "resolutions": [
    {
      "planned_id": "epic-1",
      "action": "create",
      "duplicate_key": null,
      "updates": null,
      "reason": "no similar work"
    },
    {
      "planned_id": "task-2",
      "action": "merge",
      "duplicate_key": "QUEUE-5",
      "updates": {
        "comment": "short note with new context from the meeting",
        "status": "inProgress"
      },
      "reason": "same intent as existing task"
    }
  ]
}

Rules:
- duplicate = same real work / intent, not just overlapping words
- if planned issue_type is non-empty, only merge with existing issues of the same type key
- if planned parent_key is set, only merge with existing issues with that exact parent key
- if planned parent_key is empty but parent_summary is set, only merge with existing issues
  whose parent summary matches parent_summary (case-insensitive)
- if both parent_key and parent_summary are empty, only merge with existing issues without parent
- action "merge" when duplicate_key is set; otherwise "create"
- updates.comment: optional text to add when planned has new info (description, deadline, etc.)
- updates.status: optional target status key or display to transition when reopening or
  moving forward closed/stale work; omit when no status change needed
- when uncertain, use action "create"
- duplicate_key must be from the existing list only
- return one resolution per planned_id from the input (same order is fine)"""


@dataclass(frozen=True)
class PlannedIssueForDedup:
    planned_id: str
    summary: str
    issue_type: str = ""
    parent_key: str | None = None
    parent_summary: str | None = None
    description: str = ""
    deadline: str | None = None
    priority: str | None = None


@dataclass
class DedupResolution:
    planned_id: str
    action: Literal["create", "merge"] = "create"
    duplicate_key: str | None = None
    comment: str | None = None
    target_status: str | None = None
    reason: str = ""


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


def clear_dedup_cache() -> None:
    """Clear in-memory queue issue cache (tests / forced refresh)."""
    _QUEUE_ISSUES_CACHE.clear()


def _issue_type_key(issue: dict[str, Any]) -> str:
    return str((issue.get("type") or {}).get("key") or "").lower()


def _issue_parent_key(issue: dict[str, Any]) -> str | None:
    parent = issue.get("parent")
    if isinstance(parent, dict):
        key = str(parent.get("key") or "").strip()
        return key or None
    return None


def _issue_status_key(issue: dict[str, Any]) -> str:
    return str((issue.get("status") or {}).get("key") or "").lower()


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
    """Legacy YQL builder (kept for tests). Production dedup uses full-queue LLM scan."""
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


def _compact_issue_for_llm(issue: dict[str, Any]) -> dict[str, Any]:
    st = issue.get("status") or {}
    parent = issue.get("parent") if isinstance(issue.get("parent"), dict) else {}
    return {
        "key": issue.get("key"),
        "summary": issue.get("summary"),
        "status": st.get("display") or st.get("key"),
        "type": _issue_type_key(issue) or None,
        "parent": _issue_parent_key(issue),
        "parent_summary": parent.get("display") or parent.get("summary"),
    }


def _compact_planned_for_llm(planned: PlannedIssueForDedup) -> dict[str, Any]:
    return {
        "planned_id": planned.planned_id,
        "summary": planned.summary.strip(),
        "type": planned.issue_type.strip().lower() or None,
        "parent_key": planned.parent_key,
        "parent_summary": planned.parent_summary,
        "description": (planned.description or "").strip() or None,
        "deadline": planned.deadline,
        "priority": planned.priority,
    }


async def _fetch_queue_issues_for_dedup(
    client: TrackerClient,
    queue: str,
    *,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Load all non-cancelled issues in the queue (open + closed)."""
    now = time.monotonic()
    if use_cache:
        cached = _QUEUE_ISSUES_CACHE.get(queue)
        if cached and now - cached[0] < _QUEUE_CACHE_TTL_SEC:
            return cached[1]

    exclusions = combine_yql(*build_dedup_status_exclusions())
    try:
        raw = await client.search_all_issues(exclusions, queue=queue, page_size=200)
    except Exception:
        logger.exception("Failed to load queue %s for duplicate detection", queue)
        return []

    issues = filter_out_cancelled(raw)
    if use_cache:
        _QUEUE_ISSUES_CACHE[queue] = (now, issues)
    return issues


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("LLM dedup returned non-JSON: %s", raw[:300])
        return None
    return data if isinstance(data, dict) else None


def _parse_llm_resolutions(
    raw: str,
    *,
    valid_planned_ids: set[str],
    valid_existing_keys: set[str],
) -> list[DedupResolution]:
    data = _extract_json_object(raw)
    if not data:
        return []
    rows = data.get("resolutions")
    if not isinstance(rows, list):
        return []

    out: list[DedupResolution] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        planned_id = str(row.get("planned_id") or "").strip()
        if not planned_id or planned_id not in valid_planned_ids:
            continue
        action = str(row.get("action") or "create").strip().lower()
        duplicate_key = str(row.get("duplicate_key") or "").strip() or None
        updates = row.get("updates") if isinstance(row.get("updates"), dict) else {}
        comment = str(updates.get("comment") or "").strip() or None
        target_status = str(updates.get("status") or "").strip() or None
        reason = str(row.get("reason") or "").strip()

        if action == "merge" and duplicate_key and duplicate_key in valid_existing_keys:
            out.append(
                DedupResolution(
                    planned_id=planned_id,
                    action="merge",
                    duplicate_key=duplicate_key,
                    comment=comment,
                    target_status=target_status,
                    reason=reason,
                )
            )
        else:
            out.append(
                DedupResolution(
                    planned_id=planned_id,
                    action="create",
                    reason=reason,
                )
            )
    return out


async def _llm_resolve_planned_batch(
    planned: list[PlannedIssueForDedup],
    existing_batch: list[dict[str, Any]],
) -> list[DedupResolution]:
    from core.llm import LLMClient, Message

    if not planned or not existing_batch:
        return []

    payload = {
        "planned_issues": [_compact_planned_for_llm(p) for p in planned],
        "existing_issues": existing_batch,
    }
    valid_planned_ids = {p.planned_id for p in planned}
    valid_existing_keys = {str(i.get("key") or "") for i in existing_batch}

    client = LLMClient(
        model="google/gemini-3.1-flash-lite",
        provider="openrouter",
        temperature=0.0,
        max_tokens=min(4096, 256 + 180 * len(planned)),
        max_retries=1,
    )
    try:
        resp = await client.complete(
            [
                Message(role="system", content=_DEDUP_BATCH_LLM_SYSTEM),
                Message(role="user", content=json.dumps(payload, ensure_ascii=False)),
            ]
        )
    except Exception:
        logger.exception("LLM batch duplicate detection failed")
        return []
    finally:
        await client.close()

    return _parse_llm_resolutions(
        resp.content or "",
        valid_planned_ids=valid_planned_ids,
        valid_existing_keys=valid_existing_keys,
    )


async def _llm_resolve_all_planned(
    planned: list[PlannedIssueForDedup],
    candidates: list[dict[str, Any]],
) -> list[DedupResolution]:
    """One LLM pass per existing batch; all planned items in each call."""
    compact = [_compact_issue_for_llm(issue) for issue in candidates]
    by_id: dict[str, DedupResolution] = {
        p.planned_id: DedupResolution(planned_id=p.planned_id, action="create") for p in planned
    }

    if not compact:
        return [by_id[p.planned_id] for p in planned]

    for offset in range(0, len(compact), _LLM_BATCH_SIZE):
        batch = compact[offset : offset + _LLM_BATCH_SIZE]
        for res in await _llm_resolve_planned_batch(planned, batch):
            is_merge = res.action == "merge" and res.duplicate_key
            if is_merge and by_id[res.planned_id].action != "merge":
                by_id[res.planned_id] = res

    return [by_id[p.planned_id] for p in planned]


async def resolve_planned_issues_dedup(
    client: TrackerClient,
    queue: str,
    planned: list[PlannedIssueForDedup],
) -> tuple[list[DedupResolution], dict[str, dict[str, Any]]]:
    """
    Load the queue once, match all planned issues in memory (batched LLM on existing).

    Returns resolutions in the same order as ``planned`` and a key→issue map from the snapshot.
    """
    if not planned:
        return [], {}

    issues = await _fetch_queue_issues_for_dedup(client, queue)
    by_key = {str(issue.get("key") or ""): issue for issue in issues if issue.get("key")}

    if not issues:
        return [DedupResolution(planned_id=p.planned_id, action="create") for p in planned], by_key

    resolutions = await _llm_resolve_all_planned(planned, issues)
    return resolutions, by_key


def _build_merge_comment(
    *,
    planned: PlannedIssueForDedup,
    llm_comment: str | None,
) -> str | None:
    parts: list[str] = []
    if llm_comment:
        parts.append(llm_comment.strip())
    desc = (planned.description or "").strip()
    if desc and desc not in (llm_comment or ""):
        parts.append(desc)
    if planned.deadline:
        parts.append(f"Дедлайн из плана: {planned.deadline}")
    if not parts:
        return None
    return "\n\n".join(parts)


def build_duplicate_found_response(
    existing: dict[str, Any],
    *,
    duplicate_key: str,
    dedup_reason: str = "",
    planned_create: dict[str, Any] | None = None,
    suggested_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build tool result when a duplicate is found but create was skipped."""
    out = issue_summary(existing, detailed=True)
    out["duplicate_found"] = True
    out["skipped_create"] = True
    out["key"] = duplicate_key
    if dedup_reason:
        out["dedup_reason"] = dedup_reason
    if planned_create:
        out["planned_create"] = planned_create
    if suggested_updates:
        out["suggested_updates"] = suggested_updates
    dup_summary = str(existing.get("summary") or "")
    dup_status = (existing.get("status") or {}).get("display") or ""
    out["duplicates"] = [
        {
            "key": duplicate_key,
            "summary": dup_summary,
            "status": dup_status,
        }
    ]
    out["message"] = (
        f"Найден дубликат {duplicate_key} «{dup_summary}». Новая карточка не создана. "
        "Сравни planned_create с существующей задачей и реши: если нужно дополнить контекст "
        "или поля — UpdateIssue / CreateComment / ChangeIssueStatus; если дубль полностью "
        "покрывает запрос — сообщи пользователю; для явной второй копии — "
        "tracker_create_issue с allow_duplicate=true."
    )
    return out


async def apply_duplicate_merge(
    client: TrackerClient,
    duplicate_key: str,
    existing: dict[str, Any],
    *,
    planned: PlannedIssueForDedup | None = None,
    description: str = "",
    comment: str | None = None,
    target_status: str | None = None,
    deadline: str | None = None,
    priority: str | None = None,
    assignee: str | None = None,
    story_points: int | float | None = None,
) -> dict[str, Any]:
    """Update an existing issue instead of creating a duplicate."""
    issue_key = duplicate_key.strip()
    if not issue_key:
        raise TrackerError("duplicate_key is required for merge")

    updates_applied: list[str] = []
    patch: dict[str, Any] = {}

    if deadline and not existing.get("deadline"):
        patch["deadline"] = deadline
        updates_applied.append("deadline")
    if priority and not (existing.get("priority") or {}).get("key"):
        patch["priority"] = priority
        updates_applied.append("priority")
    if assignee and not (existing.get("assignee") or {}).get("id"):
        patch["assignee"] = assignee
        updates_applied.append("assignee")
    if story_points is not None and existing.get("storyPoints") in (None, "", 0):
        patch["storyPoints"] = story_points
        updates_applied.append("story_points")

    desc = (description or (planned.description if planned else "") or "").strip()
    if desc and not str(existing.get("description") or "").strip():
        patch["description"] = desc
        updates_applied.append("description")

    current = existing
    if patch:
        current = await client.patch_issue(issue_key, patch)

    merge_comment = _build_merge_comment(
        planned=planned or PlannedIssueForDedup(planned_id="", summary=""),
        llm_comment=comment,
    )
    if not merge_comment and desc and "description" not in updates_applied:
        merge_comment = f"Новый контекст:\n{desc}"

    if merge_comment:
        await client.comment_issue(issue_key, merge_comment)
        updates_applied.append("comment")

    status_to_apply = target_status
    if not status_to_apply and _issue_status_key(current) in _CLOSED_STATUS_KEYS:
        status_to_apply = "inProgress"

    if status_to_apply:
        try:
            await client.transition_issue(issue_key, status_to_apply)
            updates_applied.append("status")
            current = await client.get_issue(issue_key)
        except TrackerError:
            logger.warning("Could not transition %s to %r", issue_key, status_to_apply)

    out = issue_summary(current, detailed=True)
    out["merged_duplicate"] = True
    out["updates_applied"] = updates_applied
    return out


async def find_duplicate_issues(
    client: TrackerClient,
    queue: str,
    *,
    summary: str,
    issue_type: str = "",
    parent_key: str | None = None,
    threshold: float | None = None,
) -> list[dict[str, Any]]:
    """
    Find existing duplicates by loading the full queue and asking an LLM.

    ``threshold`` is ignored (kept for call-site compatibility).
    """
    del threshold
    if not summary.strip():
        return []

    planned = [
        PlannedIssueForDedup(
            planned_id="0",
            summary=summary,
            issue_type=issue_type,
            parent_key=parent_key,
        )
    ]
    resolutions, by_key = await resolve_planned_issues_dedup(client, queue, planned)
    res = resolutions[0]
    if res.action != "merge" or not res.duplicate_key:
        return []
    issue = by_key.get(res.duplicate_key)
    return [issue] if issue else []


async def find_duplicate_issue(
    client: TrackerClient,
    queue: str,
    *,
    summary: str,
    issue_type: str = "",
    parent_key: str | None = None,
    threshold: float | None = None,
) -> dict[str, Any] | None:
    """Return the first LLM-detected duplicate, if any."""
    dups = await find_duplicate_issues(
        client,
        queue,
        summary=summary,
        issue_type=issue_type,
        parent_key=parent_key,
        threshold=threshold,
    )
    return dups[0] if dups else None


__all__ = [
    "DedupResolution",
    "PlannedIssueForDedup",
    "apply_duplicate_merge",
    "build_duplicate_found_response",
    "build_dedup_status_exclusions",
    "cancelled_status_names",
    "clear_dedup_cache",
    "dedup_enabled_for_backlog",
    "dedup_enabled_for_create",
    "filter_out_cancelled",
    "find_duplicate_issue",
    "find_duplicate_issues",
    "is_cancelled_issue",
    "issues_match_duplicate",
    "normalize_summary",
    "resolve_planned_issues_dedup",
    "summaries_match",
]
