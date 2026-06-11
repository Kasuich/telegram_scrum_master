"""Cosmetic streaming effects for pm_agent replies.

Two small touches, applied only to ``agent_reply`` items that the orchestrator
flagged as a live conversation with ``pm_agent``:

1. A single *status* message in italic + emoji describing the current stage
   ("планирую действия", "ищу задачи", "готовлю ответ"). Unknown stages — or a
   small random share of known ones — turn into a playful line ("колдую…").
   The same message is edited in place; the bot never spams a new one.
2. When the answer is ready the status message is deleted and the final text is
   revealed with the native ``sendMessageDraft`` stream (Telegram draws the
   animated dots), then committed with a real ``sendMessage``.

The reveal is driven by :func:`stream_output`, a mock "LLM" that yields the
answer in chunks — a stand-in for piping a real model's token stream straight
into the draft. Pacing is computed by the pure :func:`plan_pacing` so the
runtime orchestration stays trivial to unit-test.
"""

from __future__ import annotations

import html
import math
import random
from collections.abc import AsyncIterator, Awaitable, Callable

# Human-language stage labels. Keys map both real pm_agent stage ids
# (see core.stage_graph.StageId) and the synthetic "plan"/"respond" bookends.
STAGE_PHRASES: dict[str, str] = {
    "plan": "🧭 планирую действия",
    "respond": "✍️ готовлю ответ",
    "INTAKE": "📝 оформляю задачу",
    "STATUS": "✍️ обновляю статус",
    "BOARD": "🗂 раскладываю доску",
    "TRANSITION": "✅ двигаю статусы",
    "QUERY": "🔎 ищу задачи",
    "REORG": "🔧 реорганизую доску",
    "PROACTIVE": "💡 смотрю наперёд",
    "HYGIENE": "🧹 навожу порядок",
    "DIALOG": "💬 формулирую ответ",
}

# Shown when the stage is unknown, or — with FUN_PROBABILITY — instead of a
# perfectly good known stage, just for character.
FUN_PHRASES: list[str] = [
    "🪄 колдую…",
    "🤖 не галлюцинирую (вроде)…",
    "🏃 готовлю побег из дата-центра…",
    "🧠 шевелю нейронами…",
    "☕️ доливаю кофе в GPU…",
    "🔮 советуюсь с оракулом…",
    "🧩 собираю мысли в кучу…",
]

FUN_PROBABILITY = 0.25


def status_html(stage_key: str | None, rng: random.Random) -> str:
    """Render one status frame as Telegram italic HTML."""
    phrase = STAGE_PHRASES.get(stage_key or "")
    if phrase is None or rng.random() < FUN_PROBABILITY:
        phrase = rng.choice(FUN_PHRASES)
    return f"<i>{html.escape(phrase)}</i>"


def status_frames(
    stage_keys: list[str],
    rng: random.Random,
    *,
    max_frames: int = 2,
) -> list[str]:
    """Build the ordered list of status frames to edit through.

    Always starts with a "planning" beat and ends on "preparing the answer",
    optionally folding in the first real stage the agent actually visited. Kept
    short so the cosmetic flow adds little latency. Consecutive duplicates are
    collapsed.
    """
    keys: list[str] = ["plan"]
    for key in stage_keys:
        if key not in ("plan", "respond"):
            keys.append(key)
            break
    keys.append("respond")
    keys = keys[:max_frames]

    frames: list[str] = []
    for key in keys:
        frame = status_html(key, rng)
        if not frames or frame != frames[-1]:
            frames.append(frame)
    return frames


def plan_pacing(
    total: int,
    *,
    cps: float,
    interval: float,
    max_steps: int,
    max_duration: float,
) -> tuple[int, float]:
    """Pick a ``(chunk_size, delay)`` for revealing ``total`` characters.

    Targets roughly ``cps`` characters per second with one draft update every
    ``delay`` seconds, but never more than ``max_steps`` updates nor longer than
    ``max_duration`` seconds — long answers simply stream in bigger chunks so
    the effect stays snappy instead of crawling at a literal 5-6 chars/second.
    """
    if total <= 0:
        return (1, interval)

    chunk = max(1, round(cps * interval))
    steps = math.ceil(total / chunk)
    steps = min(steps, max_steps)
    if steps * interval > max_duration:
        steps = max(1, int(max_duration / interval))
    chunk = math.ceil(total / steps)
    return (chunk, interval)


async def stream_output(
    text: str,
    *,
    chunk_size: int,
    delay: float,
    sleep: Callable[[float], Awaitable[None]],
) -> AsyncIterator[str]:
    """Mock LLM token stream: yield ``text`` in ``chunk_size`` pieces.

    Stands in for piping a real model's streamed tokens into the draft — swap
    this for the model SDK's async iterator and the runtime is unchanged. The
    injected ``sleep`` keeps it deterministic under test.
    """
    step = max(1, chunk_size)
    for start in range(0, len(text), step):
        yield text[start : start + step]
        await sleep(delay)


__all__ = [
    "FUN_PHRASES",
    "FUN_PROBABILITY",
    "STAGE_PHRASES",
    "plan_pacing",
    "status_frames",
    "status_html",
    "stream_output",
]
