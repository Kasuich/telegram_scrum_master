"""Tests for backlog plan models and JSON parsing."""

from __future__ import annotations

import json

from core.backlog_plan import (
    extract_json_from_text,
    parse_backlog_plan,
    resolve_issue_type_key,
    resolve_priority_key,
)
from core.backlog_tools import (
    backlog_plan_from_steps,
    plan_json_looks_invalid,
    resolve_backlog_plan_data,
)

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


def test_parse_backlog_plan():
    plan = parse_backlog_plan(SAMPLE_PLAN)
    assert plan.create_epic is True
    assert plan.epic is not None
    assert len(plan.stories) == 1
    assert len(plan.tasks) == 1
    assert plan.tasks[0].exam_critical is True


def test_extract_json_from_fence():
    raw = "```json\n" + json.dumps(SAMPLE_PLAN, ensure_ascii=False) + "\n```"
    data = extract_json_from_text(raw)
    assert data["create_epic"] is True


def test_preview_lines():
    plan = parse_backlog_plan(SAMPLE_PLAN)
    lines = plan.preview_lines()
    assert any("эпик" in ln.lower() or "Эпик" in ln for ln in lines)


def test_resolve_issue_type_fallback():
    key, tags = resolve_issue_type_key("epic", {"task", "bug"})
    assert key == "task"
    assert "epic" in tags


def test_resolve_priority():
    assert resolve_priority_key("critical", {"critical", "normal"}) == "critical"
    assert resolve_priority_key("blocker", {"critical", "normal"}) == "critical"
    assert resolve_priority_key("", {"critical", "normal"}) == "normal"


def test_plan_json_truncated_detected():
    assert plan_json_looks_invalid('{"a": 1, ... сокращено}')
    assert not plan_json_looks_invalid('{"create_epic": true, "stories": [], "tasks": []}')


def test_resolve_plan_from_steps():
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "backlog_plan",
            "result": {"plan": SAMPLE_PLAN},
        }
    ]
    assert backlog_plan_from_steps(steps) == SAMPLE_PLAN
    data = resolve_backlog_plan_data("...обрезано...", steps=steps)
    assert data["create_epic"] is True
