"""Tests for invocation context helpers."""

from __future__ import annotations

from core.invocation import (
    InvocationContext,
    actor_label,
    format_actor_prefixed_message,
    format_transport_context_for_prompt,
    set_current_invocation_context,
)


def test_format_actor_prefixed_message_with_display_name():
    ctx = InvocationContext(
        channel="telegram",
        actor_display_name="Roman Shinkarenko",
    )
    assert format_actor_prefixed_message("создай задачу urok", ctx) == (
        "Roman Shinkarenko: создай задачу urok"
    )


def test_format_actor_prefixed_message_falls_back_to_username():
    ctx = InvocationContext(channel="telegram", actor_username="romansh")
    assert format_actor_prefixed_message("hello", ctx) == "@romansh: hello"


def test_format_actor_prefixed_message_without_author():
    assert format_actor_prefixed_message("hello", None) == "hello"


def test_actor_label_from_contextvar():
    ctx = InvocationContext(channel="telegram", actor_display_name="Ivan")
    token = set_current_invocation_context(ctx)
    try:
        assert actor_label() == "Ivan"
    finally:
        from core.invocation import reset_current_invocation_context

        reset_current_invocation_context(token)


def test_format_transport_context_for_prompt_telegram():
    ctx = InvocationContext(
        channel="telegram",
        actor_display_name="Roman Shinkarenko",
        actor_username="romansh",
        metadata={"chat_type": "group"},
        is_bot_mentioned=True,
        raw_text_without_mention="создай задачу urok",
    )
    block = format_transport_context_for_prompt(ctx)
    assert "Transport context:" in block
    assert "message_author: Roman Shinkarenko (@romansh)" in block
    assert "chat_type: group" in block
    assert "cleaned_message:" not in block
