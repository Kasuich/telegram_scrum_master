"""Tests for agent output normalizer."""

from __future__ import annotations

from core.eval.normalizer import normalize_agent_output


def test_normalize_create_from_steps() -> None:
    raw = {
        "reply": "Создал задачу",
        "steps": [
            {
                "kind": "tool_call",
                "tool_name": "tracker_create_issue",
                "arguments": {"summary": "Bug"},
            },
            {
                "kind": "tool_result",
                "tool_name": "tracker_create_issue",
                "result": {"key": "TEST-1"},
            },
        ],
    }
    out = normalize_agent_output(raw)
    assert any(op.operation == "create_task" for op in out.operations)


def test_normalize_noop() -> None:
    raw = {"reply": "Привет", "steps": []}
    out = normalize_agent_output(raw)
    assert any(op.operation == "noop" for op in out.operations)


def test_normalize_create_from_tool_args() -> None:
    """ReAct agent persists tool args under tool_args, not arguments."""
    raw = {
        "reply": "Создана TEST-1",
        "steps": [
            {
                "kind": "tool_call",
                "tool_name": "tracker_create_issue",
                "tool_args": {
                    "summary": "Сделать отчет по продажам",
                    "deadline": "2025-05-23T23:59:59",
                    "issue_type": "task",
                    "description": "Подготовить отчет по продажам.",
                },
            },
            {
                "kind": "tool_result",
                "tool_name": "tracker_create_issue",
                "result": {"key": "TEST-1"},
            },
        ],
    }
    out = normalize_agent_output(raw)
    create_ops = [op for op in out.operations if op.operation == "create_task"]
    assert len(create_ops) == 1
    assert create_ops[0].payload.get("summary") == "Сделать отчет по продажам"
    assert create_ops[0].payload.get("deadline") == "2025-05-23T23:59:59"
    assert create_ops[0].payload.get("description") == "Подготовить отчет по продажам."
