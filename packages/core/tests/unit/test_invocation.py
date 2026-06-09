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


def test_format_transport_context_identity_fields():
    ctx = InvocationContext(
        channel="telegram",
        actor_tracker_login="nukolaus",
        actor_role="dev",
        actor_default_board_id="board1",
    )
    block = format_transport_context_for_prompt(ctx)
    assert "your_tracker_login: nukolaus" in block
    assert "your_role: dev" in block
    assert "your_default_board: board1" in block


def test_format_transport_context_actor_settings():
    ctx = InvocationContext(
        channel="telegram",
        actor_settings={"tone": "friendly", "lang": "ru"},
    )
    block = format_transport_context_for_prompt(ctx)
    assert "preference_tone: friendly" in block
    assert "preference_lang: ru" in block


def test_format_transport_context_empty_settings():
    ctx = InvocationContext(channel="telegram", actor_settings={})
    block = format_transport_context_for_prompt(ctx)
    assert "preference_" not in block


def test_format_transport_context_metadata_fallback_for_tracker_login():
    ctx = InvocationContext(
        channel="telegram",
        actor_tracker_login=None,
        metadata={"tracker_login": "from_meta"},
    )
    block = format_transport_context_for_prompt(ctx)
    assert "your_tracker_login: from_meta" in block


def test_format_transport_context_metadata_fallback_for_role():
    ctx = InvocationContext(
        channel="telegram",
        actor_role=None,
        metadata={"role": "from_meta"},
    )
    block = format_transport_context_for_prompt(ctx)
    assert "your_role: from_meta" in block


def test_format_transport_context_metadata_fallback_for_board():
    ctx = InvocationContext(
        channel="telegram",
        actor_default_board_id=None,
        metadata={"default_board_id": "from_meta"},
    )
    block = format_transport_context_for_prompt(ctx)
    assert "your_default_board: from_meta" in block


def test_format_transport_context_metadata_not_used_when_explicit_set():
    ctx = InvocationContext(
        channel="telegram",
        actor_tracker_login="explicit",
        metadata={"tracker_login": "from_meta"},
    )
    block = format_transport_context_for_prompt(ctx)
    assert "your_tracker_login: explicit" in block
    assert "from_meta" not in block


def test_format_transport_context_no_identity_fields():
    ctx = InvocationContext(channel="telegram")
    block = format_transport_context_for_prompt(ctx)
    assert "your_tracker_login" not in block
    assert "your_role" not in block
    assert "your_default_board" not in block
    assert "preference_" not in block
