"""
Backlog planning tools: extract plan from summary, apply to Tracker.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from core.backlog_plan import (
    BacklogPlan,
    generate_backlog_plan,
    parse_backlog_plan,
    plan_has_issues,
    resolve_issue_type_key,
    resolve_priority_key,
)
from core.backlog_scheduler import compute_deadlines, sort_tasks_for_scheduling
from core.config import get_config
from core.invocation import get_current_invocation_context
from core.issue_dedup import (
    PlannedIssueForDedup,
    dedup_enabled_for_backlog,
    resolve_planned_issues_dedup,
)
from core.tools import platform_tool
from core.tracker import TrackerClient, TrackerError
from core.tracker_tools import _effective_queue, _resolve_login

# Phrases that signal the LLM dropped content instead of passing the full text.
_TRUNC_PHRASES = (
    "остальной текст",
    "остальное опущен",
    "текст опущен",
    "(сокращено)",
    "сокращено)",
)


def _text_looks_truncated(text: str) -> bool:
    """True when the LLM truncated the summary instead of passing it in full.

    An ellipsis counts as truncation ONLY at the very end of the text — a `…`
    in the middle is legitimate content (e.g. a quoted label), not an omission.
    """
    t = text.strip()
    lower = t.lower()
    if any(p in lower for p in _TRUNC_PHRASES):
        return True
    return t.endswith("…") or t.endswith("...")


def plan_json_looks_invalid(plan_json: str) -> bool:
    """True when LLM omitted/truncated JSON instead of passing the full plan."""
    if not str(plan_json).strip():
        return True
    s = str(plan_json).strip()
    # An ellipsis inside a JSON payload is never valid JSON — but only treat it
    # as truncation when the string isn't parseable; valid JSON wins below.
    try:
        json.loads(s)
        return False
    except json.JSONDecodeError:
        return True


def backlog_plan_from_steps(
    steps: list[dict[str, Any]], since_index: int = 0
) -> dict[str, Any] | None:
    """Last successful backlog_plan payload in this turn."""
    for step in reversed(steps[since_index:]):
        if step.get("kind") != "tool_result":
            continue
        if step.get("tool_name") != "backlog_plan":
            continue
        result = step.get("result") or {}
        if result.get("error"):
            continue
        plan = result.get("plan")
        if isinstance(plan, dict):
            return plan
    return None


def resolve_backlog_plan_data(
    plan_json: str,
    *,
    steps: list[dict[str, Any]] | None = None,
    steps_since: int = 0,
) -> dict[str, Any]:
    """Parse plan_json or fall back to the last backlog_plan tool result."""
    if not plan_json_looks_invalid(plan_json):
        data = json.loads(plan_json) if isinstance(plan_json, str) else plan_json
        if isinstance(data, dict) and "plan" in data:
            return data["plan"]
        if isinstance(data, dict):
            return data
    if steps is not None:
        fallback = backlog_plan_from_steps(steps, steps_since)
        if fallback is not None:
            return fallback
    raise ValueError(
        "plan_json пустой или обрезан. Вызови tracker_apply_backlog_plan без plan_json "
        "сразу после backlog_plan — план подставится автоматически."
    )


def _backlog_cfg() -> Any:
    return get_config().backlog


def _require_lead_or_admin_for_epic() -> dict[str, Any] | None:
    ctx = get_current_invocation_context()
    role = str(ctx.actor_role or "").strip().casefold() if ctx is not None else ""
    if role in {"lead", "admin"}:
        return None
    return {
        "error": "Epic creation is allowed only for team lead/admin",
        "required_roles": ["admin", "lead"],
        "actor_role": role or None,
    }


@platform_tool(name="backlog_plan", risk="low", scopes=["tracker:read"])
async def backlog_plan(
    text: str,
    project_title: str = "",
    queue: str = "",
) -> dict[str, Any]:
    """
    Build a structured backlog plan from a long meeting summary or lecture text.

    Does NOT create Tracker issues — use tracker_apply_backlog_plan next.
    """
    if _text_looks_truncated(text):
        return {
            "error": (
                "Summary text looks truncated. Pass the full user message to backlog_plan "
                "(do not use '...' or 'остальной текст')."
            )
        }

    q = _effective_queue(queue)
    try:
        async with TrackerClient() as client:
            meta = await client.get_queue_meta(q)
    except TrackerError as exc:
        return {
            "error": (
                f"Queue {q!r} not found ({exc}). "
                "Do not pass queue=default — omit queue to use TRACKER_QUEUE."
            )
        }
    try:
        plan = await generate_backlog_plan(
            text,
            queue_meta=meta,
            project_title=project_title,
        )
    except Exception as exc:
        return {"error": f"Failed to generate plan: {exc}"}

    if not plan_has_issues(plan):
        return {
            "error": (
                "Plan has no tasks. Pass the full summary text to backlog_plan "
                "(do not truncate with '...'). Omit the queue argument."
            ),
            "plan": plan.model_dump(),
            "preview": plan.preview_lines(),
            "stories_count": len(plan.stories),
            "tasks_count": len(plan.tasks),
            "create_epic": plan.create_epic,
            "rationale": plan.rationale,
        }

    return {
        "plan": plan.model_dump(),
        "preview": plan.preview_lines(),
        "stories_count": len(plan.stories),
        "tasks_count": len(plan.tasks),
        "create_epic": plan.create_epic,
        "rationale": plan.rationale,
        "queue": q,
    }


async def _apply_plan_impl(
    plan: BacklogPlan,
    *,
    queue: str,
    start_date: date | None = None,
    velocity_sp_per_week: float | None = None,
) -> dict[str, Any]:
    cfg = _backlog_cfg()
    start = start_date or cfg.start_date_parsed()
    velocity = (
        velocity_sp_per_week if velocity_sp_per_week is not None else cfg.velocity_sp_per_week
    )

    async with TrackerClient() as client:
        meta = await client.get_queue_meta(queue)
        type_keys = {str(t.get("key", "")).lower() for t in (meta.get("issue_types") or [])}
        type_keys.discard("")
        priority_keys = {str(p.get("key", "")).lower() for p in (meta.get("priorities") or [])}
        priority_keys.discard("")

        id_map: dict[str, str] = {}
        created: list[dict[str, Any]] = []
        merged: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        sorted_tasks = sort_tasks_for_scheduling(plan)
        deadlines = compute_deadlines(
            sorted_tasks,
            start_date=start,
            velocity_sp_per_week=velocity,
        )

        planned_specs: list[PlannedIssueForDedup] = []
        planned_order: list[tuple[Any, str | None, str | None]] = []

        def _parent_summary(local_id: str | None) -> str | None:
            if not local_id:
                return None
            if plan.epic and plan.epic.local_id == local_id:
                return plan.epic.summary
            for story in plan.stories:
                if story.local_id == local_id:
                    return story.summary
            for task in plan.tasks:
                if task.local_id == local_id:
                    return task.summary
            return None

        def _register_planned(
            issue: Any,
            *,
            parent_local_id: str | None,
            deadline: str | None,
        ) -> None:
            type_key, _ = resolve_issue_type_key(issue.issue_type, type_keys)
            priority = resolve_priority_key(issue.priority, priority_keys)
            planned_specs.append(
                PlannedIssueForDedup(
                    planned_id=issue.local_id,
                    summary=issue.summary,
                    issue_type=type_key,
                    parent_summary=_parent_summary(parent_local_id),
                    description=issue.description or "",
                    deadline=deadline,
                    priority=priority,
                )
            )
            planned_order.append((issue, parent_local_id, deadline))

        if plan.create_epic and plan.epic:
            _register_planned(plan.epic, parent_local_id=None, deadline=None)

        for story in sorted(plan.stories, key=lambda s: s.order):
            parent_local = plan.epic.local_id if plan.epic and plan.create_epic else None
            _register_planned(story, parent_local_id=parent_local, deadline=None)

        for task in sorted_tasks:
            parent_local = task.parent_local_id
            if not parent_local and plan.stories:
                parent_local = plan.stories[0].local_id
            _register_planned(
                task,
                parent_local_id=parent_local,
                deadline=deadlines.get(task.local_id),
            )

        dedup_by_id: dict[str, Any] = {}
        existing_by_key: dict[str, dict[str, Any]] = {}
        if dedup_enabled_for_backlog() and planned_specs:
            resolutions, existing_by_key = await resolve_planned_issues_dedup(
                client,
                queue,
                planned_specs,
            )
            dedup_by_id = {r.planned_id: r for r in resolutions}

        async def create_planned(
            issue: Any,
            *,
            parent_key: str | None,
            deadline: str | None,
        ) -> None:
            type_key, extra_tags = resolve_issue_type_key(issue.issue_type, type_keys)
            priority = resolve_priority_key(issue.priority, priority_keys)
            tags = list(dict.fromkeys([*issue.tags, *extra_tags]))

            assignee_login: str | None = None
            if issue.assignee_hint.strip():
                assignee_login, _ = await _resolve_login(issue.assignee_hint, client, queue)

            parent = parent_key
            if issue.parent_local_id and issue.parent_local_id in id_map:
                parent = id_map[issue.parent_local_id]

            res = dedup_by_id.get(issue.local_id)
            if res and res.action == "merge" and res.duplicate_key:
                existing = existing_by_key.get(res.duplicate_key)
                if not existing:
                    existing = await client.get_issue(res.duplicate_key)
                key = str(res.duplicate_key)
                id_map[issue.local_id] = key
                merged.append(
                    {
                        "local_id": issue.local_id,
                        "key": key,
                        "summary": issue.summary,
                        "status": (existing.get("status") or {}).get("display"),
                        "duplicate_found": True,
                        "reason": res.reason or "duplicate",
                        "planned_create": {
                            "summary": issue.summary,
                            "description": issue.description or "",
                            "priority": priority,
                            "assignee": assignee_login,
                            "deadline": deadline,
                            "story_points": issue.story_points,
                            "issue_type": type_key,
                            "parent": parent,
                        },
                        "suggested_updates": {
                            "comment": res.comment,
                            "status": res.target_status,
                        },
                    }
                )
                return

            try:
                raw = await client.create_issue(
                    queue,
                    issue.summary,
                    description=issue.description or None,
                    priority=priority,
                    assignee=assignee_login,
                    issue_type=type_key,
                    tags=tags or None,
                    deadline=deadline,
                    story_points=issue.story_points,
                    parent=parent,
                )
                key = raw.get("key", "")
                id_map[issue.local_id] = key
                created.append(
                    {
                        "local_id": issue.local_id,
                        "key": key,
                        "summary": issue.summary,
                        "issue_type": type_key,
                        "priority": priority,
                        "deadline": deadline,
                        "parent": parent,
                        "exam_critical": issue.exam_critical,
                    }
                )
            except TrackerError as exc:
                errors.append(
                    {
                        "local_id": issue.local_id,
                        "summary": issue.summary,
                        "error": str(exc),
                    }
                )

        for issue, parent_local_id, deadline in planned_order:
            parent_key = id_map.get(parent_local_id) if parent_local_id else None
            await create_planned(issue, parent_key=parent_key, deadline=deadline)

    tree_lines: list[str] = []
    if plan.create_epic and plan.epic:
        ek = id_map.get(plan.epic.local_id, "?")
        tree_lines.append(f"{ek} «{plan.epic.summary}» (epic)")
    for s in plan.stories:
        sk = id_map.get(s.local_id, "?")
        tree_lines.append(f"  {sk} «{s.summary}» (story)")
    for t in sorted_tasks[:8]:
        tk = id_map.get(t.local_id, "?")
        dl = deadlines.get(t.local_id, "")
        tree_lines.append(f"    {tk} «{t.summary}» deadline={dl}")
    if len(sorted_tasks) > 8:
        tree_lines.append(f"    … ещё {len(sorted_tasks) - 8} задач")

    critical = [c for c in created if c.get("exam_critical") or c.get("priority") == "critical"]

    return {
        "created_count": len(created),
        "merged_count": len(merged),
        "skipped_count": len(merged),
        "error_count": len(errors),
        "created": created,
        "merged": merged,
        "skipped": merged,
        "errors": errors,
        "id_map": id_map,
        "tree": tree_lines,
        "critical": critical[:5],
        "epic_key": id_map.get(plan.epic.local_id) if plan.epic else None,
    }


@platform_tool(name="tracker_apply_backlog_plan", risk="medium", scopes=["tracker:write"])
async def tracker_apply_backlog_plan(
    plan_json: str = "",
    queue: str = "",
    start_date: str = "",
    velocity_sp_per_week: str = "",
) -> dict[str, Any]:
    """
    Create epic, stories, and tasks in Tracker from a BacklogPlan.

    Leave plan_json empty after backlog_plan — the server reuses the last plan
    from the same turn (do not copy/paste or truncate JSON).
    """
    q = _effective_queue(queue)
    try:
        from core.backlog_context import get_pending_backlog_plan

        pending = get_pending_backlog_plan()
        if plan_json_looks_invalid(plan_json) and pending is not None:
            data = pending
        else:
            data = resolve_backlog_plan_data(plan_json)
        plan = parse_backlog_plan(data)
    except Exception as exc:
        return {"error": f"Invalid plan_json: {exc}"}

    if not plan_has_issues(plan):
        return {
            "error": "Plan is empty — run backlog_plan with the full summary first.",
            "created_count": 0,
            "error_count": 0,
            "created": [],
            "errors": [],
        }
    if plan.create_epic and plan.epic:
        forbidden = _require_lead_or_admin_for_epic()
        if forbidden is not None:
            return forbidden

    start: date | None = None
    if start_date.strip():
        start = date.fromisoformat(start_date.strip())
    velocity: float | None = None
    if velocity_sp_per_week.strip():
        velocity = float(velocity_sp_per_week)

    return await _apply_plan_impl(
        plan,
        queue=q,
        start_date=start,
        velocity_sp_per_week=velocity,
    )


__all__ = ["backlog_plan", "tracker_apply_backlog_plan", "_apply_plan_impl"]
