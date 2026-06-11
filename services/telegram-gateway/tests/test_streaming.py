from __future__ import annotations

import random

from telegram_gateway.streaming import (
    CURSOR,
    FUN_PHRASES,
    STAGE_PHRASES,
    THINKING_SEQUENCE,
    reveal_frames,
    status_html,
    thinking_html,
)


def test_status_html_known_stage_is_italic() -> None:
    # rng forced past the fun-probability threshold → keep the real phrase.
    rng = random.Random()
    rng.random = lambda: 0.99  # type: ignore[method-assign]
    out = status_html("QUERY", rng)
    assert out == f"<i>{STAGE_PHRASES['QUERY']}</i>"


def test_status_html_unknown_stage_falls_back_to_fun() -> None:
    rng = random.Random(0)
    out = status_html("NOPE", rng)
    inner = out.removeprefix("<i>").removesuffix("</i>")
    assert inner in FUN_PHRASES


def test_status_html_known_stage_can_be_replaced_by_fun() -> None:
    rng = random.Random()
    rng.random = lambda: 0.0  # type: ignore[method-assign]  # always below threshold
    out = status_html("QUERY", rng)
    inner = out.removeprefix("<i>").removesuffix("</i>")
    assert inner in FUN_PHRASES


def test_thinking_html_cycles_the_sequence() -> None:
    rng = random.Random()
    rng.random = lambda: 0.99  # type: ignore[method-assign]  # keep real phrases
    # index 0 and len(sequence) map to the same stage key.
    assert thinking_html(0, rng) == thinking_html(len(THINKING_SEQUENCE), rng)
    assert thinking_html(0, rng) == f"<i>{STAGE_PHRASES[THINKING_SEQUENCE[0]]}</i>"


def test_reveal_frames_grow_prefixes_with_cursor() -> None:
    text = "abcdefghij" * 3  # 30 chars
    frames = reveal_frames(text, cps=6, interval=0.8, max_steps=10, max_duration=6)
    assert frames
    prev = 0
    for frame in frames:
        assert frame.endswith(CURSOR)
        body = frame[: -len(CURSOR)]
        assert text.startswith(body)
        assert len(body) > prev
        prev = len(body)
    # Frames stop before the full text (final HTML is rendered by the caller).
    assert prev < len(text)


def test_reveal_frames_respect_max_duration() -> None:
    text = "x" * 5000
    frames = reveal_frames(text, cps=6, interval=0.8, max_steps=100, max_duration=6)
    assert len(frames) <= int(6 / 0.8)


def test_reveal_frames_empty_text() -> None:
    assert reveal_frames("", cps=6, interval=0.8, max_steps=10, max_duration=6) == []
