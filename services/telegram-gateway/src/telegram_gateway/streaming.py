"""Cosmetic streaming effects for pm_agent replies.

Two touches for live conversations with ``pm_agent``:

1. A *thinking* status message in italic + emoji describing the current stage
   ("планирую действия", "ищу задачи", "готовлю ответ"). It is sent **early**
   (while the agent is still working, before the answer exists) and the same
   message is edited in place through a few stage beats — the bot never spams a
   new one. Unknown stages — or a small random share of known ones — turn into a
   playful line ("колдую…"). Used in **all** chats.
2. The answer reveal:
   - **Private chats**: native ``sendMessageDraft`` streaming. Telegram renders
     a live, growing draft bubble with animated dots; the draft is ephemeral
     (~30 s) so a real ``sendMessage`` must follow to persist the answer. This
     method only works in private chats (a group target errors with
     ``TEXTDRAFT_PEER_INVALID``).
   - **Group chats**: no streaming — the whole message is just sent normally.

The pure helpers here (:func:`status_html`, :func:`plan_pacing`,
:func:`stream_output`) keep the runtime orchestration trivial to unit-test.
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

# Order the thinking status cycles through while the agent works. Real stages
# aren't known until the agent finishes, so the early animation uses a sensible
# generic arc: plan → search → prepare answer (then loops).
THINKING_SEQUENCE: tuple[str, ...] = ("plan", "QUERY", "respond")

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

FUN_PROBABILITY = 0.2


def status_html(stage_key: str | None, rng: random.Random) -> str:
    """Render one status frame as Telegram italic HTML."""
    phrase = STAGE_PHRASES.get(stage_key or "")
    if phrase is None or rng.random() < FUN_PROBABILITY:
        phrase = rng.choice(FUN_PHRASES)
    return f"<i>{html.escape(phrase)}</i>"


def thinking_html(index: int, rng: random.Random) -> str:
    """Render the ``index``-th thinking beat, cycling :data:`THINKING_SEQUENCE`."""
    key = THINKING_SEQUENCE[index % len(THINKING_SEQUENCE)]
    return status_html(key, rng)


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
    the effect stays snappy and well within the draft's ~30 s lifetime.
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
    "THINKING_SEQUENCE",
    "plan_pacing",
    "status_html",
    "stream_output",
    "thinking_html",
]
