"""Tests for backlog deadline scheduling."""

from __future__ import annotations

from datetime import date

from core.backlog_plan import PlannedIssue, parse_backlog_plan
from core.backlog_scheduler import compute_deadlines, sort_tasks_for_scheduling

SAMPLE_PLAN = {
    "create_epic": True,
    "rationale": "test",
    "epic": {
        "local_id": "epic-1",
        "issue_type": "epic",
        "summary": "Epic",
        "order": 0,
    },
    "stories": [
        {
            "local_id": "story-mvp",
            "issue_type": "story",
            "summary": "Story",
            "parent_local_id": "epic-1",
            "order": 0,
        }
    ],
    "tasks": [],
}


def test_sort_tasks_exam_critical_first_within_order():
    plan = parse_backlog_plan(
        {
            **SAMPLE_PLAN,
            "tasks": [
                {
                    "local_id": "t2",
                    "issue_type": "task",
                    "summary": "Later",
                    "order": 1,
                    "story_points": 3,
                    "parent_local_id": "story-mvp",
                },
                {
                    "local_id": "t1",
                    "issue_type": "task",
                    "summary": "Critical first",
                    "order": 0,
                    "story_points": 2,
                    "exam_critical": True,
                    "parent_local_id": "story-mvp",
                },
            ],
        }
    )
    sorted_tasks = sort_tasks_for_scheduling(plan)
    assert sorted_tasks[0].local_id == "t1"


def test_compute_deadlines_monotonic():
    tasks = [
        PlannedIssue(local_id="a", summary="A", story_points=5, order=0),
        PlannedIssue(local_id="b", summary="B", story_points=5, order=1),
    ]
    start = date(2026, 6, 1)
    dl = compute_deadlines(tasks, start_date=start, velocity_sp_per_week=10)
    assert dl["a"] <= dl["b"]
    assert dl["a"] >= "2026-06-01"
