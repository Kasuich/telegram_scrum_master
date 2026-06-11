"""Tests for issue duplicate detection."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from core.issue_dedup import (
    DedupResolution,
    PlannedIssueForDedup,
    build_dedup_find_queries,
    build_dedup_status_exclusions,
    clear_dedup_cache,
    filter_out_cancelled,
    find_duplicate_issue,
    find_duplicate_issues,
    issues_match_duplicate,
    normalize_summary,
    resolve_planned_issues_dedup,
    summaries_match,
)

ENV = {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test_db",
    "YC_API_KEY": "test_api_key_12345678901234567890",
    "YC_FOLDER_ID": "b1g1234567890abcdef",
    "TRACKER_TOKEN": "test_tracker_token_12345678901234567890",
    "TRACKER_ORG_ID": "12345678901234567890",
    "TRACKER_QUEUE": "TEST",
}


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_dedup_cache()
    yield
    clear_dedup_cache()


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
async def test_find_duplicate_issue_uses_llm_on_full_queue():
    client = AsyncMock()
    client.search_all_issues.return_value = [
        {
            "key": "TEST-5",
            "summary": "Интеграция Telegram",
            "type": {"key": "task"},
            "status": {"display": "Закрыт", "key": "closed"},
        },
        {
            "key": "TEST-9",
            "summary": "Другое",
            "type": {"key": "task"},
            "status": {"display": "Открыт", "key": "open"},
        },
    ]
    with patch(
        "core.issue_dedup._llm_resolve_all_planned",
        new_callable=AsyncMock,
        return_value=[
            DedupResolution(
                planned_id="0",
                action="merge",
                duplicate_key="TEST-5",
                reason="same work",
            )
        ],
    ) as mock_llm:
        dup = await find_duplicate_issue(
            client,
            "TEST",
            summary="Интеграция Telegram",
            issue_type="task",
            parent_key=None,
        )

    assert dup is not None
    assert dup["key"] == "TEST-5"
    client.search_all_issues.assert_awaited_once()
    mock_llm.assert_awaited_once()


@patch.dict("os.environ", ENV)
@pytest.mark.asyncio
async def test_find_duplicate_issues_returns_llm_keys_in_order():
    client = AsyncMock()
    issues = [
        {
            "key": "TEST-5",
            "summary": "Сделать рабочий дайджест",
            "type": {"key": "task"},
            "status": {"display": "Открыт", "key": "open"},
        },
        {
            "key": "TEST-9",
            "summary": "Сделать рабочий дайджест по проекту",
            "type": {"key": "task"},
            "status": {"display": "В работе", "key": "inProgress"},
        },
    ]
    client.search_all_issues.return_value = issues
    with patch(
        "core.issue_dedup._llm_resolve_all_planned",
        new_callable=AsyncMock,
        return_value=[
            DedupResolution(
                planned_id="0",
                action="merge",
                duplicate_key="TEST-9",
            )
        ],
    ):
        dups = await find_duplicate_issues(
            client,
            "TEST",
            summary="Сделать рабочий дайджест",
            issue_type="task",
            parent_key=None,
        )

    assert len(dups) == 1
    assert dups[0]["key"] == "TEST-9"


@patch.dict("os.environ", ENV)
@pytest.mark.asyncio
async def test_find_duplicate_issues_empty_when_llm_finds_none():
    client = AsyncMock()
    client.search_all_issues.return_value = [
        {
            "key": "TEST-3",
            "summary": "Совсем про другое",
            "type": {"key": "task"},
            "status": {"display": "Открыт", "key": "open"},
        },
    ]
    with patch(
        "core.issue_dedup._llm_resolve_all_planned",
        new_callable=AsyncMock,
        return_value=[DedupResolution(planned_id="0", action="create")],
    ):
        dups = await find_duplicate_issues(
            client,
            "TEST",
            summary="Сделать рабочий дайджест",
            issue_type="task",
            parent_key=None,
        )

    assert dups == []


@patch.dict("os.environ", ENV)
@pytest.mark.asyncio
async def test_resolve_planned_batch_loads_queue_once():
    client = AsyncMock()
    client.search_all_issues.return_value = [
        {"key": "TEST-1", "summary": "A", "type": {"key": "task"}, "status": {"key": "open"}},
    ]
    planned = [
        PlannedIssueForDedup(planned_id="t1", summary="Task A", issue_type="task"),
        PlannedIssueForDedup(planned_id="t2", summary="Task B", issue_type="task"),
    ]
    with patch(
        "core.issue_dedup._llm_resolve_all_planned",
        new_callable=AsyncMock,
        return_value=[
            DedupResolution(planned_id="t1", action="create"),
            DedupResolution(planned_id="t2", action="create"),
        ],
    ) as mock_llm:
        resolutions, by_key = await resolve_planned_issues_dedup(client, "TEST", planned)

    assert len(resolutions) == 2
    client.search_all_issues.assert_awaited_once()
    mock_llm.assert_awaited_once()
    assert mock_llm.await_args.args[0] == planned
    assert "TEST-1" in by_key
