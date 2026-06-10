"""
Goal plan: decompose a user message into ordered goal items.

The planner sits above the single-stage ReAct loop: each goal gets its own
frozen stage, payload, intent, entities, success criteria, and missing-info
list. DIALOG is a special case with no tools.
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
    '{"goals": [{"stage": "STAGE", "payload": "фрагмент сообщения", '
    '"intent": "краткое намерение", "entities": {"ключ": "значение"}, '
    '"success_criteria": "условие успеха", "missing_info": ["что неизвестно"], '
    '"rationale": "кратко"}]}\n'
    "stage — одно из: INTAKE STATUS BOARD TRANSITION QUERY REORG PROACTIVE HYGIENE DIALOG.\n"
    "INTAKE — создать задачу/спринт. STATUS — «Имя: …» статус-апдейт. "
    "BOARD — оформить доску из саммари. TRANSITION — закрыть/сменить статус. "
    "QUERY — спросить о доске (только чтение). REORG — переназначить/спринт/приоритет. "
    "PROACTIVE — проверка просрочки/дайджест. HYGIENE — навести порядок. "
    "DIALOG — болтовня/привет/вопрос о боте (не про доску).\n"
    "intent — что именно нужно сделать (на русском).\n"
    "entities — извлечённые сущности (person, metric, sprint, queue и т.д.).\n"
    "success_criteria — как поймём, что цель достигнута (на русском).\n"
    "missing_info — ТОЛЬКО то, без чего действие НЕВОЗМОЖНО выполнить совсем. "
    "НЕ включай: дедлайн, дату, SP, приоритет, описание — агент добавит их если нужно. "
    "Для STATUS/QUERY: missing_info=[] если задача упомянута по имени или ключу. "
    "Для INTAKE: missing_info=[] если есть хотя бы summary. "
    "Оставляй missing_info пустым при малейшем сомнении — лучше попробовать, чем спросить.\n"
    "Если одно действие — один элемент. Максимум 6. "
    "payload — точный фрагмент исходного сообщения для этого действия."
)


@dataclass
class GoalItem:
    stage: StageId
    payload: str
    intent: str
    entities: dict[str, str] = field(default_factory=dict)
    success_criteria: str = ""
    missing_info: list[str] = field(default_factory=list)
    rationale: str | None = None


@dataclass
class GoalPlan:
    items: list[GoalItem] = field(default_factory=list)
    is_dialog: bool = False

    @classmethod
    def single(cls, stage: StageId, message: str, *, rationale: str | None = None) -> GoalPlan:
        if stage == StageId.DIALOG:
            return cls.dialog(message)
        return cls(
            items=[
                GoalItem(
                    stage=stage,
                    payload=message,
                    intent="",
                    entities={},
                    success_criteria="",
                    missing_info=[],
                    rationale=rationale,
                )
            ]
        )

    @classmethod
    def dialog(cls, message: str) -> GoalPlan:
        return cls(
            items=[
                GoalItem(
                    StageId.DIALOG,
                    message,
                    intent="",
                    entities={},
                    success_criteria="",
                    missing_info=[],
                )
            ],
            is_dialog=True,
        )


def serialize_plan(plan: GoalPlan) -> dict[str, Any]:
    return {
        "items": [
            {
                "stage": item.stage.value,
                "payload": item.payload,
                "intent": item.intent,
                "entities": item.entities,
                "success_criteria": item.success_criteria,
                "missing_info": item.missing_info,
                "rationale": item.rationale,
            }
            for item in plan.items
        ],
        "is_dialog": plan.is_dialog,
    }


def deserialize_plan(data: dict[str, Any] | None) -> GoalPlan | None:
    if not data:
        return None
    items: list[GoalItem] = []
    for raw in data.get("items") or []:
        try:
            stage = StageId(str(raw.get("stage", "")))
        except ValueError:
            continue
        payload = str(raw.get("payload") or "").strip()
        if not payload:
            continue
        items.append(
            GoalItem(
                stage=stage,
                payload=payload,
                intent=str(raw.get("intent") or ""),
                entities=raw.get("entities") if isinstance(raw.get("entities"), dict) else {},
                success_criteria=str(raw.get("success_criteria") or ""),
                missing_info=raw.get("missing_info") if isinstance(raw.get("missing_info"), list) else [],
                rationale=raw.get("rationale"),
            )
        )
    return GoalPlan(items=items, is_dialog=bool(data.get("is_dialog")))


def _parse_decompose_json(raw: str, message: str) -> GoalPlan | None:
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
    goals = data.get("goals")
    if not isinstance(goals, list) or not goals:
        return None

    items: list[GoalItem] = []
    for entry in goals[:_MAX_SCENARIOS]:
        if not isinstance(entry, dict):
            continue
        stage_raw = str(entry.get("stage", "")).strip().upper()
        if stage_raw not in {s.value for s in _VALID_STAGES}:
            continue
        payload = str(entry.get("payload") or message).strip()
        if not payload:
            payload = message
        items.append(
            GoalItem(
                stage=StageId(stage_raw),
                payload=payload,
                intent=str(entry.get("intent") or ""),
                entities=entry.get("entities") if isinstance(entry.get("entities"), dict) else {},
                success_criteria=str(entry.get("success_criteria") or ""),
                missing_info=entry.get("missing_info") if isinstance(entry.get("missing_info"), list) else [],
                rationale=entry.get("rationale"),
            )
        )
    if not items:
        return None
    return _finalize_goal_plan(items)


def _finalize_goal_plan(items: list[GoalItem]) -> GoalPlan:
    """Drop DIALOG when actionable goals exist; cap at 6."""
    actionable = [i for i in items if i.stage != StageId.DIALOG]
    if actionable:
        items = actionable[:_MAX_SCENARIOS]
        return GoalPlan(items=items, is_dialog=False)
    if len(items) == 1 and items[0].stage == StageId.DIALOG:
        return GoalPlan.dialog(items[0].payload)
    return GoalPlan(items=items[:_MAX_SCENARIOS], is_dialog=False)


async def decompose_goal_llm(message: str) -> GoalPlan:
    """One LLM call with structured JSON output. Falls back to rules on failure."""
    from core.llm import LLMClient, Message

    client = LLMClient(model="google/gemini-3.1-flash-lite", provider="openrouter", temperature=0.0, max_tokens=512, max_retries=0)
    try:
        resp = await client.complete(
            [
                Message(role="system", content=_DECOMPOSE_SYSTEM),
                Message(role="user", content=message[:4000]),
            ]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("goal decompose failed, falling back to rules: %s", exc)
        return _rules_fallback(message)
    finally:
        await client.close()

    plan = _parse_decompose_json(resp.content or "", message)
    if plan is None:
        logger.info("goal decompose returned unparseable JSON, falling back to rules")
        return _rules_fallback(message)
    return plan


def _rules_fallback(message: str) -> GoalPlan:
    sid = detect_stage_rules(message) or StageId.QUERY
    return GoalPlan.single(sid, message)


async def build_goal_plan(message: str, *, use_llm: bool = True) -> GoalPlan:
    """Build the goal plan. Tests/resume use ``use_llm=False`` (deterministic, no network)."""
    if not use_llm:
        return _rules_fallback(message)
    return await decompose_goal_llm(message)


__all__ = [
    "GoalItem",
    "GoalPlan",
    "build_goal_plan",
    "decompose_goal_llm",
    "deserialize_plan",
    "serialize_plan",
]
