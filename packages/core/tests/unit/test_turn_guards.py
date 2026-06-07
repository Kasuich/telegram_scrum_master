"""Tests for per-turn tool guards."""

from __future__ import annotations

import pytest
from core.turn_guards import (
    check_turn_tool_guard,
    message_has_backlog_intent,
    message_has_close_intent,
    message_has_create_intent,
    message_has_status_update_intent,
)


def test_create_intent():
    assert message_has_create_intent("Создай Коле задачу MCP")
    assert not message_has_close_intent("Создай Коле задачу MCP")


@pytest.mark.asyncio
async def test_block_close_after_create_async():
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_create_issue",
            "result": {"key": "DARKHORSE-9"},
        },
    ]
    err = await check_turn_tool_guard(
        tool_name="tracker_close_issue",
        tool_args={"issue_key": "DARKHORSE-9"},
        turn_user_message="Создай Коле MCP",
        steps=steps,
        steps_before_turn=0,
        queue_key="TEST",
    )
    assert err is not None
    assert "Запрещено закрывать" in err


@pytest.mark.asyncio
async def test_block_second_create():
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_create_issue",
            "result": {"key": "DARKHORSE-9"},
        },
    ]
    err = await check_turn_tool_guard(
        tool_name="tracker_create_issue",
        tool_args={"summary": "x", "assignee": "nukolaus"},
        turn_user_message="Создай задачу",
        steps=steps,
        steps_before_turn=0,
        queue_key="TEST",
    )
    assert err is not None


def test_backlog_intent_long_text():
    text = "x" * 900
    assert message_has_backlog_intent(text)
    assert not message_has_create_intent(text)


def test_backlog_intent_markers():
    assert message_has_backlog_intent("Резюме лекции: бот для хакатона")
    assert not message_has_create_intent("Резюме лекции: бот для хакатона")


def test_status_update_not_backlog():
    text = (
        "Коля: добавил meeting_summarizer с резюме и саммари. "
        "Запросы «суммаризируй…» идут через call_agent."
    )
    assert message_has_status_update_intent(text)
    assert not message_has_backlog_intent(text)


@pytest.mark.asyncio
async def test_status_update_blocks_backlog_allows_find():
    text = "Коля: добавил meeting_summarizer, тесты проходят."
    err_backlog = await check_turn_tool_guard(
        tool_name="backlog_plan",
        tool_args={"text": text},
        turn_user_message=text,
        steps=[],
        steps_before_turn=0,
        queue_key="TEST",
    )
    assert err_backlog is not None
    err_find = await check_turn_tool_guard(
        tool_name="tracker_find_issues",
        tool_args={"assignee": "Коля", "summary_hint": "meeting_summarizer"},
        turn_user_message=text,
        steps=[],
        steps_before_turn=0,
        queue_key="TEST",
    )
    assert err_find is None


@pytest.mark.asyncio
async def test_status_update_comment_requires_summarizer_first():
    text = "Коля: статус по задаче"
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_find_issues",
            "result": {"count": 1, "issues": [{"key": "TEST-1"}]},
        },
    ]
    err_comment = await check_turn_tool_guard(
        tool_name="tracker_comment_issue",
        tool_args={"issue_key": "TEST-1", "text": "raw"},
        turn_user_message=text,
        steps=steps,
        steps_before_turn=0,
        queue_key="TEST",
    )
    assert err_comment is not None
    assert "meeting_summarizer" in err_comment

    steps_with_summary = [
        *steps,
        {
            "kind": "tool_result",
            "tool_name": "call_agent",
            "tool_args": {"target_agent": "meeting_summarizer"},
            "result": "**Статус**\n\n## Сделано\n- x",
        },
    ]
    err_ok = await check_turn_tool_guard(
        tool_name="tracker_comment_issue",
        tool_args={"issue_key": "TEST-1", "text": "**Статус**"},
        turn_user_message=text,
        steps=steps_with_summary,
        steps_before_turn=0,
        queue_key="TEST",
    )
    assert err_ok is None


@pytest.mark.asyncio
async def test_call_agent_summarizer_only_for_status_update():
    err = await check_turn_tool_guard(
        tool_name="call_agent",
        tool_args={"target_agent": "meeting_summarizer", "message": "hi"},
        turn_user_message="Создай задачу MCP",
        steps=[],
        steps_before_turn=0,
        queue_key="TEST",
    )
    assert err is not None


@pytest.mark.asyncio
async def test_backlog_blocks_single_create():
    text = "Резюме лекции: " + "текст " * 200
    err = await check_turn_tool_guard(
        tool_name="tracker_create_issue",
        tool_args={"summary": "x"},
        turn_user_message=text,
        steps=[],
        steps_before_turn=0,
        queue_key="TEST",
    )
    assert err is not None
    assert "backlog_plan" in err


@pytest.mark.asyncio
async def test_backlog_allows_apply_after_plan():
    text = "Оформи доску из саммари " + "x" * 500
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "backlog_plan",
            "result": {"plan": {}, "tasks_count": 3},
        },
    ]
    err = await check_turn_tool_guard(
        tool_name="tracker_apply_backlog_plan",
        tool_args={"plan_json": "{}"},
        turn_user_message=text,
        steps=steps,
        steps_before_turn=0,
        queue_key="TEST",
    )
    assert err is None
