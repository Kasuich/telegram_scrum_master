"""Tests for tracker_board_snapshot and tracker_read_comments (mocked client)."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from core.tracker_tools import tracker_board_snapshot, tracker_read_comments


def _issue(key, *, status="Открыт", assignee=None, deadline=None, sp=None):
    return {
        "key": key,
        "summary": f"summary {key}",
        "status": {"display": status},
        "assignee": {"display": assignee} if assignee else None,
        "deadline": deadline,
        "storyPoints": sp,
    }


class _FakeClient:
    def __init__(self, issues):
        self._issues = issues

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def search_issues(self, query, *, queue=None, limit=200):
        return self._issues

    async def list_comments(self, issue_key, *, per_page=50):
        return [
            {"createdBy": {"display": "Коля"}, "createdAt": "2026-06-01", "text": "первый"},
            {"createdBy": {"display": "Рома"}, "createdAt": "2026-06-02", "text": "второй"},
        ]


async def test_board_snapshot_aggregates():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    issues = [
        _issue("D-1", status="Открыт", assignee="Коля", deadline=yesterday, sp=3),
        _issue("D-2", status="В работе", assignee=None, deadline=None, sp=None),
        _issue("D-3", status="Открыт", assignee="Рома", deadline=tomorrow, sp=5),
        _issue("D-4", status="Закрыт", assignee="Коля", deadline=yesterday, sp=2),
    ]
    with patch("core.tracker_tools.TrackerClient", lambda: _FakeClient(issues)):
        snap = await tracker_board_snapshot(queue="DARKHORSE", at_risk_days=3)

    assert snap["queue"] == "DARKHORSE"
    # Closed (D-4) excluded by default
    assert snap["total"] == 3
    assert snap["by_status"].get("Открыт") == 2
    assert snap["by_assignee"].get("(не назначен)") == 1
    # D-1 overdue (yesterday), D-3 at risk (tomorrow within 3 days)
    assert any(i["key"] == "D-1" for i in snap["overdue"])
    assert any(i["key"] == "D-3" for i in snap["at_risk"])
    assert any(i["key"] == "D-2" for i in snap["unassigned"])
    assert any(i["key"] == "D-2" for i in snap["no_estimate"])
    assert any(i["key"] == "D-2" for i in snap["no_deadline"])


async def test_board_snapshot_include_closed():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    issues = [_issue("D-4", status="Закрыт", assignee="Коля", deadline=yesterday, sp=2)]
    with patch("core.tracker_tools.TrackerClient", lambda: _FakeClient(issues)):
        snap = await tracker_board_snapshot(queue="DARKHORSE", include_closed=True)
    assert snap["total"] == 1
    # Terminal issue is never overdue/at-risk even if its deadline passed
    assert snap["overdue"] == []


async def test_read_comments():
    with patch("core.tracker_tools.TrackerClient", lambda: _FakeClient([])):
        out = await tracker_read_comments("D-1", limit=10)
    assert out["issue_key"] == "D-1"
    assert out["count"] == 2
    assert out["comments"][0]["author"] == "Коля"
    assert out["comments"][-1]["text"] == "второй"
