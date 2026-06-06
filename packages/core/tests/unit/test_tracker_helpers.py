"""Tests for tracker_tool_helpers and queue resolution."""

from __future__ import annotations

import pytest
from core.backlog_plan import ensure_queue_meta, parse_backlog_plan, plan_has_issues
from core.config import reload_config
from core.tracker_tool_helpers import (
    apply_open_status_filter_to_yql,
    build_find_yql,
    filter_terminal_issues,
    normalize_deadline,
)
from core.tracker_tools import _effective_queue


def test_build_find_yql_excludes_terminal_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRACKER_SEARCH_ALL_STATUSES", raising=False)
    yql = build_find_yql(summary_hint="CI", assignee_login="login")
    assert 'Status: !"Closed"' in yql
    assert 'Status: !"Закрыт"' in yql
    assert 'Status: !"Отменена"' in yql
    assert "Summary:" in yql


def test_build_find_yql_explicit_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRACKER_SEARCH_ALL_STATUSES", raising=False)
    yql = build_find_yql(summary_hint="CI", status="Закрыт")
    assert 'Status: "Закрыт"' in yql
    assert 'Status: !"' not in yql


def test_apply_open_status_filter_skips_when_status_in_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRACKER_SEARCH_ALL_STATUSES", raising=False)
    q = apply_open_status_filter_to_yql('Summary: "MCP" AND Status: "Закрыт"')
    assert q == 'Summary: "MCP" AND Status: "Закрыт"'


def test_filter_terminal_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRACKER_SEARCH_ALL_STATUSES", raising=False)
    issues = [
        {"key": "A-1", "status": {"key": "open", "display": "Открыт"}},
        {"key": "A-2", "status": {"key": "closed", "display": "Закрыт"}},
        {"key": "A-3", "status": {"key": "cancelled", "display": "Отменена"}},
    ]
    filtered = filter_terminal_issues(issues)
    assert [i["key"] for i in filtered] == ["A-1"]


def test_effective_queue_ignores_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACKER_QUEUE", "DARKHORSE")
    reload_config()
    try:
        assert _effective_queue("") == "DARKHORSE"
        assert _effective_queue("default") == "DARKHORSE"
        assert _effective_queue("DEFAULT") == "DARKHORSE"
        assert _effective_queue("DARKHORSE") == "DARKHORSE"
    finally:
        monkeypatch.setenv("TRACKER_QUEUE", "TEST")
        reload_config()


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
