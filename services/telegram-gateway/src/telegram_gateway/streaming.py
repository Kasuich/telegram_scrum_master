"""Cosmetic streaming effects for pm_agent replies.

Two small touches for live conversations with ``pm_agent``:

1. A *thinking* status message in italic + emoji describing the current stage
   ("планирую действия", "ищу задачи", "готовлю ответ"). It is sent **early**
   (while the agent is still working, before the answer exists) and the same
   message is edited in place through a few stage beats — the bot never spams a
   new one. Unknown stages — or a small random share of known ones — turn into a
   playful line ("колдую…").
2. When the answer is ready the status message is deleted and the text is
   revealed with a typing effect: one message grown via repeated
   ``editMessageText`` (the reliable, universally-supported mechanism), then a
   final edit that applies the HTML formatting.

The pure helpers here (:func:`status_html`, :func:`reveal_frames`) keep the
runtime orchestration trivial to unit-test.
"""

from __future__ import annotations

import html
import math
import random

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

# Typing cursor appended to intermediate (not-yet-final) reveal frames.
CURSOR = "▍"


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


def reveal_frames(
    text: str,
    *,
    cps: float,
    interval: float,
    max_steps: int,
    max_duration: float,
) -> list[str]:
    """Plan the intermediate typing frames for ``text``.

    Reveals progressively at roughly ``cps`` characters per second, one edit
    every ``interval`` seconds, but never more than ``max_steps`` edits nor
    longer than ``max_duration`` seconds — long answers simply reveal in bigger
    chunks so the effect stays snappy instead of crawling at a literal 5-6
    chars/second. Each frame is a strict, growing prefix with a typing cursor;
    the caller renders the final full text as HTML separately.
    """
    total = len(text)
    if total == 0:
        return []

    chunk = max(1, round(cps * interval))
    steps = math.ceil(total / chunk)
    steps = min(steps, max_steps)
    if steps * interval > max_duration:
        steps = max(1, int(max_duration / interval))
    chunk = math.ceil(total / steps)

    frames: list[str] = []
    pos = chunk
    while pos < total:
        frames.append(text[:pos] + CURSOR)
        pos += chunk
    return frames


__all__ = [
    "CURSOR",
    "FUN_PHRASES",
    "FUN_PROBABILITY",
    "STAGE_PHRASES",
    "THINKING_SEQUENCE",
    "reveal_frames",
    "status_html",
    "thinking_html",
]
