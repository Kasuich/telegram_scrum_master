"""Tests for tracker_tool_helpers and queue resolution."""

from __future__ import annotations

import pytest
from core.backlog_plan import ensure_queue_meta, parse_backlog_plan, plan_has_issues
from core.tracker_tool_helpers import normalize_deadline
from core.tracker_tools import _effective_queue


@pytest.fixture(autouse=True)
def _tracker_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACKER_QUEUE", "DARKHORSE")


def test_effective_queue_ignores_default() -> None:
    assert _effective_queue("") == "DARKHORSE"
    assert _effective_queue("default") == "DARKHORSE"
    assert _effective_queue("DEFAULT") == "DARKHORSE"
    assert _effective_queue("DARKHORSE") == "DARKHORSE"


def test_normalize_deadline_iso_with_time() -> None:
    assert normalize_deadline("2026-06-19 18:00") == "2026-06-19"
    assert normalize_deadline("2026-06-19T18:00:00") == "2026-06-19"


def test_normalize_deadline_russian() -> None:
    assert normalize_deadline("7 июня 2026") == "2026-06-07"


def test_normalize_deadline_dotted() -> None:
    assert normalize_deadline("07.06.2026") == "2026-06-07"


def test_normalize_deadline_invalid() -> None:
    result = normalize_deadline("next friday")
    assert isinstance(result, dict)
    assert "error" in result


def test_ensure_queue_meta_fills_defaults() -> None:
    meta = ensure_queue_meta({})
    assert {t["key"] for t in meta["issue_types"]} >= {"epic", "task"}
    assert {p["key"] for p in meta["priorities"]} >= {"critical", "normal"}


def test_plan_has_issues() -> None:
    empty = parse_backlog_plan({"create_epic": False, "tasks": [], "stories": []})
    assert not plan_has_issues(empty)
    with_tasks = parse_backlog_plan(
        {
            "tasks": [
                {
                    "local_id": "t1",
                    "summary": "Do thing",
                }
            ]
        }
    )
    assert plan_has_issues(with_tasks)
