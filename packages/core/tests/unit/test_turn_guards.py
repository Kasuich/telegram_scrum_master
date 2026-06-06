"""Tests for per-turn tool guards."""

from __future__ import annotations

import pytest
from core.turn_guards import (
    check_turn_tool_guard,
    message_has_backlog_intent,
    message_has_close_intent,
    message_has_create_intent,
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
