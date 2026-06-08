"""
Turn plan: decompose a user message into ordered PM scenarios.

The planner sits above the single-stage ReAct loop: each scenario gets its own
frozen stage and payload slice. DIALOG is a special case with no tools.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from core.stage_graph import StageId
from core.stage_router import detect_stage_rules

logger = logging.getLogger(__name__)

_MAX_SCENARIOS = 6
_ACTION_STAGES = frozenset(
    {
        StageId.INTAKE,
        StageId.STATUS,
        StageId.BOARD,
        StageId.TRANSITION,
        StageId.QUERY,
        StageId.REORG,
        StageId.PROACTIVE,
        StageId.HYGIENE,
    }
)
_VALID_STAGES = _ACTION_STAGES | {StageId.DIALOG}

_DECOMPOSE_SYSTEM = (
    "Ты — планировщик PM-агента над доской Яндекс Трекера. "
    "Разбей сообщение пользователя на упорядоченный список независимых PM-действий. "
    "Верни ТОЛЬКО валидный JSON без markdown:\n"
    '{"scenarios": [{"stage": "STAGE", "payload": "фрагмент сообщения", '
    '"rationale": "кратко"}]}\n'
    "stage — одно из: INTAKE STATUS BOARD TRANSITION QUERY REORG PROACTIVE HYGIENE DIALOG.\n"
    "INTAKE — создать задачу/спринт. STATUS — «Имя: …» статус-апдейт. "
    "BOARD — оформить доску из саммари. TRANSITION — закрыть/сменить статус. "
    "QUERY — спросить о доске (только чтение). REORG — переназначить/спринт/приоритет. "
    "PROACTIVE — проверка просрочки/дайджест. HYGIENE — навести порядок. "
    "DIALOG — болтовня/привет/вопрос о боте (не про доску).\n"
    "Если одно действие — один элемент. Максимум 6. "
    "payload — точный фрагмент исходного сообщения для этого действия."
)


@dataclass
class ScenarioItem:
    stage: StageId
    payload: str
    rationale: str | None = None


@dataclass
class TurnPlan:
    items: list[ScenarioItem] = field(default_factory=list)
    is_dialog: bool = False

    @classmethod
    def single(cls, stage: StageId, message: str, *, rationale: str | None = None) -> TurnPlan:
        if stage == StageId.DIALOG:
            return cls.dialog(message)
        return cls(items=[ScenarioItem(stage=stage, payload=message, rationale=rationale)])

    @classmethod
    def dialog(cls, message: str) -> TurnPlan:
        return cls(
            items=[ScenarioItem(StageId.DIALOG, message)],
            is_dialog=True,
        )


def serialize_plan(plan: TurnPlan) -> dict[str, Any]:
    return {
        "items": [
            {
                "stage": item.stage.value,
                "payload": item.payload,
                "rationale": item.rationale,
            }
            for item in plan.items
        ],
        "is_dialog": plan.is_dialog,
    }


def deserialize_plan(data: dict[str, Any] | None) -> TurnPlan | None:
    if not data:
        return None
    items: list[ScenarioItem] = []
    for raw in data.get("items") or []:
        try:
            stage = StageId(str(raw.get("stage", "")))
        except ValueError:
            continue
        payload = str(raw.get("payload") or "").strip()
        if not payload:
            continue
        items.append(
            ScenarioItem(
                stage=stage,
                payload=payload,
                rationale=raw.get("rationale"),
            )
        )
    return TurnPlan(items=items, is_dialog=bool(data.get("is_dialog")))


def _parse_decompose_json(raw: str, message: str) -> TurnPlan | None:
    text = raw.strip()
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        return None

    items: list[ScenarioItem] = []
    for entry in scenarios[:_MAX_SCENARIOS]:
        if not isinstance(entry, dict):
            continue
        stage_raw = str(entry.get("stage", "")).strip().upper()
        if stage_raw not in {s.value for s in _VALID_STAGES}:
            continue
        payload = str(entry.get("payload") or message).strip()
        if not payload:
            payload = message
        items.append(
            ScenarioItem(
                stage=StageId(stage_raw),
                payload=payload,
                rationale=entry.get("rationale"),
            )
        )
    if not items:
        return None
    return _finalize_plan(items)


def _finalize_plan(items: list[ScenarioItem]) -> TurnPlan:
    """Drop DIALOG when actionable scenarios exist; cap at 6."""
    actionable = [i for i in items if i.stage != StageId.DIALOG]
    if actionable:
        items = actionable[:_MAX_SCENARIOS]
        return TurnPlan(items=items, is_dialog=False)
    if len(items) == 1 and items[0].stage == StageId.DIALOG:
        return TurnPlan.dialog(items[0].payload)
    return TurnPlan(items=items[:_MAX_SCENARIOS], is_dialog=False)


async def decompose_turn_llm(message: str) -> TurnPlan:
    """One LLM call with structured JSON output. Falls back to rules on failure."""
    from core.llm import LLMClient, Message

    client = LLMClient(model="yandexgpt", temperature=0.0, max_tokens=512, max_retries=0)
    try:
        resp = await client.complete(
            [
                Message(role="system", content=_DECOMPOSE_SYSTEM),
                Message(role="user", content=message[:4000]),
            ]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("turn decompose failed, falling back to rules: %s", exc)
        return _rules_fallback(message)
    finally:
        await client.close()

    plan = _parse_decompose_json(resp.content or "", message)
    if plan is None:
        logger.info("turn decompose returned unparseable JSON, falling back to rules")
        return _rules_fallback(message)
    return plan


def _rules_fallback(message: str) -> TurnPlan:
    sid = detect_stage_rules(message) or StageId.QUERY
    return TurnPlan.single(sid, message)


async def plan_turn(message: str, *, use_llm: bool = True) -> TurnPlan:
    """Build the turn plan. Tests/resume use ``use_llm=False`` (deterministic, no network)."""
    if not use_llm:
        return _rules_fallback(message)
    return await decompose_turn_llm(message)


__all__ = [
    "ScenarioItem",
    "TurnPlan",
    "decompose_turn_llm",
    "deserialize_plan",
    "plan_turn",
    "serialize_plan",
]
