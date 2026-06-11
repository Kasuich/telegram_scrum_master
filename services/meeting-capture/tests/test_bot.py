"""Unit tests for end-of-meeting detection in PlaywrightTelemostBot.

These exercise the pure detection logic and wait_until_finished against a fake
page — no real browser. The fix targets the "stuck in recording" bug where the
bot never noticed the meeting had ended.
"""

from __future__ import annotations

import asyncio

import pytest
from meeting_capture.bot import (
    PlaywrightTelemostBot,
    is_noise_participant_name,
    parse_speaking_aria_label,
)
from meeting_capture.config import CaptureSettings


class FakePage:
    """Minimal stand-in for a Playwright page driving wait_until_finished."""

    def __init__(self, scripted_bodies: list[str], *, url: str = "https://telemost.yandex.ru/j/1"):
        self._bodies = scripted_bodies
        self._idx = 0
        self.url = url
        self._closed = False

    def is_closed(self) -> bool:
        return self._closed

    async def _current_body(self) -> str:
        body = self._bodies[min(self._idx, len(self._bodies) - 1)]
        self._idx += 1
        return body


def _bot() -> PlaywrightTelemostBot:
    bot = PlaywrightTelemostBot(CaptureSettings())
    bot._ALONE_GRACE_SEC = 0  # type: ignore[attr-defined]
    return bot


@pytest.mark.parametrize(
    "body",
    [
        "Встреча завершена",
        "Вы вышли из звонка",
        "Покинули встречу",
        "You have left the call",
        "Join again",
    ],
)
def test_end_screen_patterns(body: str) -> None:
    assert PlaywrightTelemostBot._looks_like_end_screen(body) is True


def test_end_screen_negative() -> None:
    assert PlaywrightTelemostBot._looks_like_end_screen("Идёт обсуждение бюджета") is False


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Коля, говорит", "Коля"),
        ("Roman is speaking", "Roman"),
        ("говорит: Алиса", "Алиса"),
        ("Микрофон включен", None),
    ],
)
def test_parse_speaking_aria_label(label: str, expected: str | None) -> None:
    assert parse_speaking_aria_label(label) == expected


def test_is_noise_participant_name_filters_ui_chrome() -> None:
    assert is_noise_participant_name("Тарифы для бизнеса") is True
    assert is_noise_participant_name("Николай") is False
    assert (
        is_noise_participant_name(
            "PM Assistant (recording)",
            bot_display_name="PM Assistant (recording)",
        )
        is True
    )


def test_left_call_url_detected() -> None:
    bot = _bot()
    bot._page = type("P", (), {"url": "https://telemost.yandex.ru/feedback"})()  # type: ignore[assignment]
    assert bot._looks_like_left_call_url() is True


def test_left_call_url_active_meeting() -> None:
    bot = _bot()
    bot._page = type("P", (), {"url": "https://telemost.yandex.ru/j/abc-def"})()  # type: ignore[assignment]
    assert bot._looks_like_left_call_url() is False


async def test_wait_until_finished_detects_end_screen() -> None:
    bot = _bot()
    page = FakePage(["идёт встреча", "Встреча завершена"])

    async def fake_body() -> str:
        return await page._current_body()

    bot._page = page  # type: ignore[assignment]
    bot._body_text = fake_body  # type: ignore[assignment]
    bot._other_participant_signal = lambda: _true()  # type: ignore[assignment]

    reason = await bot.wait_until_finished(stop_event=asyncio.Event(), max_duration_sec=60)
    assert reason == "meeting ended"


async def test_wait_until_finished_detects_alone() -> None:
    bot = _bot()
    page = FakePage(["идёт встреча"])
    signals = iter([True, False])  # others present once, then bot is alone

    async def fake_body() -> str:
        return await page._current_body()

    async def fake_signal() -> bool:
        try:
            return next(signals)
        except StopIteration:
            return False

    bot._page = page  # type: ignore[assignment]
    bot._body_text = fake_body  # type: ignore[assignment]
    bot._other_participant_signal = fake_signal  # type: ignore[assignment]

    reason = await bot.wait_until_finished(stop_event=asyncio.Event(), max_duration_sec=60)
    assert reason == "alone in meeting"


async def test_wait_until_finished_stop_requested() -> None:
    bot = _bot()
    bot._page = FakePage(["идёт встреча"])  # type: ignore[assignment]
    bot._body_text = _empty_body  # type: ignore[assignment]
    bot._other_participant_signal = lambda: _true()  # type: ignore[assignment]
    event = asyncio.Event()
    event.set()
    reason = await bot.wait_until_finished(stop_event=event, max_duration_sec=60)
    assert reason == "stop requested"


async def _true() -> bool:
    return True


async def _empty_body() -> str:
    return ""
