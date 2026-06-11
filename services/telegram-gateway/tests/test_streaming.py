from __future__ import annotations

import random

import pytest
from telegram_gateway.streaming import (
    FUN_PHRASES,
    STAGE_PHRASES,
    plan_pacing,
    status_frames,
    status_html,
    stream_output,
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


def test_status_frames_bookends_and_no_consecutive_dupes() -> None:
    rng = random.Random()
    rng.random = lambda: 0.99  # type: ignore[method-assign]  # keep real phrases
    frames = status_frames(["QUERY"], rng)
    assert frames
    assert all(f.startswith("<i>") and f.endswith("</i>") for f in frames)
    assert all(frames[i] != frames[i + 1] for i in range(len(frames) - 1))
    # First beat is always "planning".
    assert frames[0] == f"<i>{STAGE_PHRASES['plan']}</i>"


def test_status_frames_empty_stages_still_produces_frames() -> None:
    rng = random.Random(1)
    frames = status_frames([], rng)
    assert len(frames) >= 1


def test_plan_pacing_targets_cps() -> None:
    chunk, delay = plan_pacing(30, cps=6, interval=0.8, max_steps=10, max_duration=6)
    assert delay == 0.8
    assert chunk == round(6 * 0.8)  # ~5 chars per update


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
