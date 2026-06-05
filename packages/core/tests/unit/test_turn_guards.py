"""Tests for per-turn tool guards."""

import pytest

from core.turn_guards import (
    check_turn_tool_guard,
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
