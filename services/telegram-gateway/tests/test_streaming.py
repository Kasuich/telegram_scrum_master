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


def test_plan_pacing_targets_cps() -> None:
    chunk, delay = plan_pacing(30, cps=6, interval=0.8, max_steps=10, max_duration=6)
    assert delay == 0.8
    assert chunk == round(6 * 0.8)  # ~5 chars per draft update


def test_plan_pacing_caps_steps_by_duration() -> None:
    chunk, delay = plan_pacing(5000, cps=6, interval=0.8, max_steps=100, max_duration=6)
    steps = -(-5000 // chunk)  # ceil
    assert steps <= int(6 / 0.8)


def test_plan_pacing_empty() -> None:
    assert plan_pacing(0, cps=6, interval=0.8, max_steps=10, max_duration=6) == (1, 0.8)


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
