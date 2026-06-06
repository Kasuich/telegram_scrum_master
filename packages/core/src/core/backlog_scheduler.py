"""Deadline scheduling from story points and task order."""

from __future__ import annotations

from datetime import date, timedelta

from core.backlog_plan import BacklogPlan, PlannedIssue


def _parent_order(plan: BacklogPlan, issue: PlannedIssue) -> int:
    if issue.parent_local_id:
        for s in plan.stories:
            if s.local_id == issue.parent_local_id:
                return s.order
        if plan.epic and plan.epic.local_id == issue.parent_local_id:
            return 0
    return issue.order


def sort_tasks_for_scheduling(plan: BacklogPlan) -> list[PlannedIssue]:
    tasks = list(plan.tasks)
    tasks.sort(
        key=lambda t: (
            _parent_order(plan, t),
            t.order,
            0 if (t.exam_critical or t.priority == "critical") else 1,
        )
    )
    return tasks


def compute_deadlines(
    tasks: list[PlannedIssue],
    *,
    start_date: date,
    velocity_sp_per_week: float,
    critical_factor: float = 0.7,
) -> dict[str, str]:
    """
    Return local_id -> deadline ISO date (YYYY-MM-DD).

    Cumulative story points map to weeks at given velocity.
    exam_critical / critical tasks use a shorter offset (critical_factor).
    """
    if velocity_sp_per_week <= 0:
        velocity_sp_per_week = 20.0

    result: dict[str, str] = {}
    cumulative = 0.0

    for task in tasks:
        sp = float(task.story_points or 1)
        cumulative += sp
        weeks = cumulative / velocity_sp_per_week
        if task.exam_critical or task.priority == "critical":
            weeks *= critical_factor
        days = max(1, int(weeks * 7))
        deadline = start_date + timedelta(days=days)
        result[task.local_id] = deadline.isoformat()

    return result


__all__ = ["sort_tasks_for_scheduling", "compute_deadlines"]
