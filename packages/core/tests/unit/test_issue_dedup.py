"""Tests for issue duplicate detection."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.issue_dedup import (
    build_dedup_find_queries,
    build_dedup_status_exclusions,
    filter_out_cancelled,
    find_duplicate_issue,
    issues_match_duplicate,
    normalize_summary,
    summaries_match,
)
from core.tracker import TrackerError

ENV = {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test_db",
    "YC_API_KEY": "test_api_key_12345678901234567890",
    "YC_FOLDER_ID": "b1g1234567890abcdef",
    "TRACKER_TOKEN": "test_tracker_token_12345678901234567890",
    "TRACKER_ORG_ID": "12345678901234567890",
    "TRACKER_QUEUE": "TEST",
}


def test_normalize_summary():
    assert normalize_summary("  Интеграция   Telegram  ") == "интеграция telegram"


def test_summaries_match_exact_and_fuzzy():
    assert summaries_match("MCP корнер кейсы", "MCP корнер кейсы", threshold=0.88)
    assert summaries_match("Интеграция Telegram", "Интеграция Telegram API", threshold=0.88)
    assert not summaries_match("Разные задачи", "Совсем другое", threshold=0.88)


def test_build_dedup_find_queries_quotes_special_chars():
    summary = "MVP: автоматическое (встроенная запись vs собственная)"
    queries = build_dedup_find_queries(summary=summary, issue_type="story")
    assert queries
    for q in queries:
        assert f"Summary: {summary}" not in q
        assert "Summary: " in q
        assert q.count("(") == q.count(")")


@patch.dict("os.environ", ENV)
@pytest.mark.asyncio
async def test_find_duplicate_skips_invalid_yql_queries():
    summary = "MVP: запись (встроенная)"
    client = AsyncMock()
    client.search_issues.side_effect = [
        TrackerError("bad query", status_code=422),
        [
            {
                "key": "TEST-7",
                "summary": summary,
                "type": {"key": "task"},
                "status": {"display": "Открыт", "key": "open"},
            }
        ],
    ]
    dup = await find_duplicate_issue(
        client,
        "TEST",
        summary=summary,
        issue_type="task",
        parent_key=None,
    )
    assert dup is not None
    assert dup["key"] == "TEST-7"
    assert client.search_issues.await_count == 2


def test_build_dedup_status_exclusions():
    parts = build_dedup_status_exclusions()
    assert any("Отменена" in p for p in parts)
    assert all(p.startswith("Status: !") for p in parts)


def test_issues_match_duplicate_respects_type_and_parent():
    candidate = {
        "summary": "MVP из чата",
        "type": {"key": "story"},
        "status": {"display": "Закрыт", "key": "closed"},
        "parent": {"key": "TEST-1"},
    }
    assert issues_match_duplicate(
        "MVP из чата",
        candidate,
        type_key="story",
        parent_key="TEST-1",
    )
    assert not issues_match_duplicate(
        "MVP из чата",
        candidate,
        type_key="task",
        parent_key="TEST-1",
    )
    assert not issues_match_duplicate(
        "MVP из чата",
        candidate,
        type_key="story",
        parent_key="TEST-2",
    )


def test_filter_out_cancelled():
    issues = [
        {"key": "A-1", "status": {"key": "open", "display": "Открыт"}},
        {"key": "A-2", "status": {"key": "cancelled", "display": "Отменена"}},
    ]
    assert len(filter_out_cancelled(issues)) == 1


@patch.dict("os.environ", ENV)
@pytest.mark.asyncio
async def test_find_duplicate_issue_returns_best_match():
    client = AsyncMock()
    client.search_issues.return_value = [
        {
            "key": "TEST-5",
            "summary": "Интеграция Telegram",
            "type": {"key": "task"},
            "status": {"display": "Закрыт", "key": "closed"},
        }
    ]
    dup = await find_duplicate_issue(
        client,
        "TEST",
        summary="Интеграция Telegram",
        issue_type="task",
        parent_key=None,
    )
    assert dup is not None
    assert dup["key"] == "TEST-5"
