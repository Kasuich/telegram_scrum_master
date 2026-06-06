"""Tests for tracker_apply_backlog_plan with mocked Tracker."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from core.backlog_tools import tracker_apply_backlog_plan
SAMPLE_PLAN = {
    "create_epic": True,
    "rationale": "Несколько потоков работ",
    "epic": {
        "local_id": "epic-1",
        "issue_type": "epic",
        "summary": "Бот-помощник",
        "description": "MVP хакатона",
        "order": 0,
    },
    "stories": [
        {
            "local_id": "story-mvp",
            "issue_type": "story",
            "summary": "MVP из чата",
            "parent_local_id": "epic-1",
            "order": 0,
            "story_points": 8,
        }
    ],
    "tasks": [
        {
            "local_id": "task-1",
            "issue_type": "task",
            "summary": "Интеграция Telegram",
            "parent_local_id": "story-mvp",
            "priority": "critical",
            "story_points": 5,
            "exam_critical": True,
            "order": 0,
        }
    ],
}


@pytest.mark.asyncio
async def test_apply_backlog_plan_uses_pending_context():
    from core.backlog_context import set_pending_backlog_plan

    set_pending_backlog_plan(SAMPLE_PLAN)
    counter = {"n": 0}

    async def fake_create(queue, summary, **kwargs):
        counter["n"] += 1
        return {"key": f"TEST-{counter['n']}", "summary": summary}

    with patch("core.backlog_tools.TrackerClient") as mock_cls:
        client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = client
        client.get_queue_meta.return_value = {
            "queue_key": "TEST",
            "issue_types": [{"key": "epic"}, {"key": "story"}, {"key": "task"}],
            "priorities": [{"key": "critical"}, {"key": "normal"}],
        }
        client.create_issue.side_effect = fake_create

        result = await tracker_apply_backlog_plan(plan_json="", queue="TEST")

    assert result["created_count"] == 3


@pytest.mark.asyncio
async def test_apply_backlog_plan_mock():
    counter = {"n": 0}

    async def fake_create(queue, summary, **kwargs):
        counter["n"] += 1
        return {"key": f"TEST-{counter['n']}", "summary": summary}

    with patch("core.backlog_tools.TrackerClient") as mock_cls:
        client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = client
        client.get_queue_meta.return_value = {
            "queue_key": "TEST",
            "issue_types": [
                {"key": "epic", "name": "Epic"},
                {"key": "story", "name": "Story"},
                {"key": "task", "name": "Task"},
            ],
            "priorities": [
                {"key": "critical", "name": "Critical"},
                {"key": "normal", "name": "Normal"},
            ],
        }
        client.create_issue.side_effect = fake_create

        result = await tracker_apply_backlog_plan(
            plan_json=json.dumps(SAMPLE_PLAN),
            queue="TEST",
            start_date="2026-06-01",
            velocity_sp_per_week="20",
        )

    assert result["created_count"] == 3
    assert result["epic_key"] == "TEST-1"
    assert len(result["tree"]) >= 2
