from __future__ import annotations

import random

import pytest
from telegram_gateway.streaming import (
    FUN_PHRASES,
    STAGE_PHRASES,
    THINKING_SEQUENCE,
    plan_pacing,
    status_html,
    stream_output,
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


_PACE = dict(cps=60, interval=0.4, max_steps=20, min_duration=0.8, max_duration=7)


def test_plan_pacing_short_message_is_quick_not_draggy() -> None:
    # A 30-char message should reveal in ~min_duration, not crawl.
    chunk, delay = plan_pacing(30, **_PACE)
    steps = -(-30 // chunk)  # ceil
    total_time = steps * delay
    assert total_time <= 1.2  # ≈ min_duration, snappy
    assert steps <= 3


def test_plan_pacing_long_message_capped_but_smooth() -> None:
    # A long message caps at max_duration but in many small steps (not 1 jump).
    chunk, delay = plan_pacing(2000, **_PACE)
    steps = -(-2000 // chunk)  # ceil
    total_time = steps * delay
    assert total_time <= 7.0 + 1e-9  # never longer than max_duration
    assert steps >= 10  # smooth, not a couple of huge chunks
    assert chunk < 300  # no giant single reveals


def test_plan_pacing_duration_scales_with_length() -> None:
    def total_time(n: int) -> float:
        chunk, delay = plan_pacing(n, **_PACE)
        return -(-n // chunk) * delay

    # Mid-size reveals take longer than tiny ones, up to the cap.
    assert total_time(40) < total_time(400) <= 7.0 + 1e-9


def test_plan_pacing_empty() -> None:
    assert plan_pacing(0, **_PACE) == (1, 0.4)


@pytest.mark.asyncio
async def test_stream_output_yields_full_text_in_chunks() -> None:
    async def _noop(_: float) -> None:
        return None

    text = "Нашла три задачи в очереди"
    chunks = [
        chunk
        async for chunk in stream_output(text, chunk_size=5, delay=0, sleep=_noop)
    ]
    assert "".join(chunks) == text
    assert all(len(c) <= 5 for c in chunks)
    assert len(chunks) == -(-len(text) // 5)  # ceil(len / 5)
