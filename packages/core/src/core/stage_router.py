"""
Stage router: classify a turn's input message into exactly one StageId.

Rules first (cheap regex/keyword predicates, reusing ``turn_guards``), then a
strict-enum LLM classifier for ambiguous messages. The chosen stage is computed
ONCE per turn by the runner and frozen — never re-derived per tool call.

QUERY is the safe default: it is read-only, so a mis-route there can never harm
the board (worst case the agent reports instead of acting).

The async classifier is skippable (``use_llm=False``) so unit tests run without
an LLM; cron and user messages enter the same router.
"""

from __future__ import annotations

import logging

from core.stage_graph import StageId
from core.turn_guards import (
    message_has_backlog_intent,
    message_has_close_intent,
    message_has_create_intent,
    message_has_status_update_intent,
    normalize_text,
)

logger = logging.getLogger(__name__)

# Marker sets for the new stages (rules R3, R5, R6, R8).
_REORG_MARKERS = (
    "переназначь",
    "переназначить",
    "поменяй родител",
    "смени родител",
    "перенеси в спринт",
    "в спринт",
    "подними приоритет",
    "понизь приоритет",
    "раздели задачу",
    "разбей задачу",
    "объедини",
    "перевесь",
)
_PROACTIVE_MARKERS = (
    "просроч",
    "без исполнит",
    "не назначен",
    "без движения",
    "застрял",
    "дайджест",
    "что горит",
    "под угрозой",
    "проверь задачи",
    "проверь доску",
    "проверь просроч",
)
_HYGIENE_MARKERS = (
    "наведи порядок",
    "проверь оформлен",
    "дедуп",
    "дубл",
    "заполни пропуск",
    "нормализуй приоритет",
    "гигиена",
)
_QUERY_MARKERS = (
    "что на доске",
    "что у ",
    "что просрочено",
    "статус эпика",
    "стендап",
    "standup",
    "покажи",
    "сколько задач",
    "список задач",
    "какие задачи",
    "отчёт по доске",
    "отчет по доске",
)

_TRANSITION_MARKERS = (
    "переведи",
    "перемести",
    "перенеси в статус",
    "смени статус",
    "в работу",
    "в закрыто",
    "верни в",
)

_VALID_STAGES = {s.value for s in StageId}


def detect_stage_rules(message: str) -> StageId | None:
    """First-match-wins rule classifier. Returns None when ambiguous (-> LLM)."""
    if message_has_status_update_intent(message):  # R1: «Имя: …»
        return StageId.STATUS
    if message_has_backlog_intent(message):  # R2: long text / "оформи доску"
        return StageId.BOARD
    t = normalize_text(message)
    if any(m in t for m in _REORG_MARKERS):  # R3
        return StageId.REORG
    if message_has_close_intent(message) or any(m in t for m in _TRANSITION_MARKERS):  # R4
        return StageId.TRANSITION
    if any(m in t for m in _PROACTIVE_MARKERS):  # R5
        return StageId.PROACTIVE
    if any(m in t for m in _HYGIENE_MARKERS):  # R6
        return StageId.HYGIENE
    if message_has_create_intent(message):  # R7
        return StageId.INTAKE
    if any(m in t for m in _QUERY_MARKERS):  # R8
        return StageId.QUERY
    return None  # R9 -> LLM classifier


_CLASSIFIER_SYSTEM = (
    "Ты — классификатор намерения для PM-агента над доской Яндекс Трекера. "
    "Верни СТРОГО ОДНО слово, без знаков и пояснений, из набора: "
    "INTAKE STATUS BOARD TRANSITION QUERY REORG PROACTIVE HYGIENE DIALOG.\n"
    "INTAKE — создать новую задачу. "
    "STATUS — обновить задачу / комментарий-статус «Имя: …». "
    "BOARD — оформить доску из длинного саммари (эпик/стори/таски). "
    "TRANSITION — закрыть / сменить статус. "
    "QUERY — спросить о состоянии доски (только чтение). "
    "REORG — переназначить / сменить родителя / спринт / приоритет. "
    "PROACTIVE — проверка по расписанию (просрочка, без исполнителя, дайджест). "
    "HYGIENE — навести порядок / дедуп / заполнить пропуски. "
    "DIALOG — болтовня, приветствие, вопрос о боте (не про доску).\n"
    "Если сомневаешься — верни QUERY."
)


async def classify_stage_llm(message: str) -> StageId:
    """Strict-enum LLM fallback. Defaults to QUERY (read-only) on any failure."""
    from core.llm import LLMClient, Message

    client = LLMClient(model="yandexgpt", temperature=0.0, max_tokens=8, max_retries=0)
    try:
        resp = await client.complete(
            [
                Message(role="system", content=_CLASSIFIER_SYSTEM),
                Message(role="user", content=message[:2000]),
            ]
        )
    except Exception as exc:  # noqa: BLE001 — classifier must never break the turn
        logger.warning("stage classifier failed, defaulting to QUERY: %s", exc)
        return StageId.QUERY
    finally:
        await client.close()

    raw = (resp.content or "").strip().upper()
    for stage_value in _VALID_STAGES:
        if stage_value in raw:
            return StageId(stage_value)
    logger.info("stage classifier returned unrecognized %r, defaulting to QUERY", raw)
    return StageId.QUERY


async def detect_stage(message: str, *, use_llm: bool = True) -> StageId:
    """Rules first; LLM fallback when ``use_llm`` and no rule matched."""
    sid = detect_stage_rules(message)
    if sid is not None:
        return sid
    if not use_llm:
        return StageId.QUERY  # safe default, no network (test path)
    return await classify_stage_llm(message)


__all__ = ["detect_stage", "detect_stage_rules", "classify_stage_llm"]
