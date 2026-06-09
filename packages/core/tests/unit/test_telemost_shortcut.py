"""Tests for Telemost link short-circuit helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from core.invocation import InvocationContext
from core.telemost_shortcut import (
    extract_telemost_url,
    format_meeting_capture_reply,
    try_meeting_capture_shortcut,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://telemost.yandex.ru/j/12345678901234567", "https://telemost.yandex.ru/j/12345678901234567"),
        (
            "заходи https://telemost.yandex.ru/j/abc123.",
            "https://telemost.yandex.ru/j/abc123",
        ),
        (
            "https://telemost.360.yandex.ru/j/xyz789",
            "https://telemost.360.yandex.ru/j/xyz789",
        ),
        (
            "telemost.360.yandex.ru/live/meeting-id",
            "https://telemost.360.yandex.ru/live/meeting-id",
        ),
        ("создай задачу", None),
        ("https://example.com/j/123", None),
    ],
)
def test_extract_telemost_url(text: str, expected: str | None) -> None:
    assert extract_telemost_url(text) == expected


def test_format_meeting_capture_reply() -> None:
    reply = format_meeting_capture_reply({"meeting_id": "m-42", "status": "recording"})
    assert "m-42" in reply
    assert "Иду на встречу" in reply


@pytest.mark.asyncio
async def test_try_meeting_capture_shortcut_returns_none_without_link() -> None:
    result = await try_meeting_capture_shortcut("привет", "s1")
    assert result is None


@pytest.mark.asyncio
async def test_try_meeting_capture_shortcut_schedules_capture() -> None:
    url = "https://telemost.yandex.ru/j/12345678901234567"
    ctx = InvocationContext(channel="telegram", chat_id="-1001")
    with patch(
        "core.telemost_shortcut.schedule_meeting_capture",
        AsyncMock(return_value={"meeting_id": "m1", "status": "recording"}),
    ) as schedule:
        result = await try_meeting_capture_shortcut(f"встреча {url}", "s1", context=ctx)

    schedule.assert_awaited_once_with(url, target_chat_id="-1001")
    assert result is not None
    assert "m1" in (result.reply or "")
    assert result.steps[0]["kind"] == "meeting_capture_scheduled"


@pytest.mark.asyncio
async def test_try_meeting_capture_shortcut_surfaces_errors() -> None:
    url = "https://telemost.yandex.ru/j/12345678901234567"
    with patch(
        "core.telemost_shortcut.schedule_meeting_capture",
        AsyncMock(side_effect=RuntimeError("connection refused")),
    ):
        result = await try_meeting_capture_shortcut(url, "s1")

    assert result is not None
    assert "Не удалось" in (result.reply or "")
    assert result.steps[0]["kind"] == "meeting_capture_error"
