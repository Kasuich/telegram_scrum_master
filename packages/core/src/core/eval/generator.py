"""Synthetic scenario and user_text generation."""

from __future__ import annotations

import json
import logging
from datetime import date

from core.eval.schemas import SyntheticScenario
from core.eval.suites import FIXTURE_CASES, SUITE_PROMPTS
from core.llm import LLMClient, Message

logger = logging.getLogger(__name__)

_SCENARIO_SYSTEM = """You generate structured eval scenarios for a Yandex Tracker PM agent.
Return strict JSON only with keys:
goal, expected_behavior, initial_state, expected_operations, forbidden_operations, metadata.
initial_state.tasks is a list of existing tasks with key, summary, description, status.
expected_operations and forbidden_operations are lists of {operation, match?}."""


async def generate_scenario(
    *,
    suite: str,
    difficulty: str,
    current_date: str,
    model: str,
    index: int,
) -> SyntheticScenario:
    if index < len(FIXTURE_CASES):
        base = FIXTURE_CASES[index % len(FIXTURE_CASES)]
        if base.suite == suite:
            return base.model_copy(update={"difficulty": difficulty})  # type: ignore[arg-type]

    prompt = SUITE_PROMPTS.get(suite, "Eval scenario for PM agent.")
    client = LLMClient(model=model, provider="openrouter", temperature=0.7, max_tokens=2000)
    user = (
        f"Suite: {suite}\nDifficulty: {difficulty}\nDate: {current_date}\n"
        f"Guideline: {prompt}\n"
        f"Generate unique scenario #{index + 1}."
    )
    try:
        response = await client.complete(
            [Message(role="system", content=_SCENARIO_SYSTEM), Message(role="user", content=user)]
        )
        raw = json.loads(_extract_json(response.content or "{}"))
        return SyntheticScenario(
            goal=str(raw.get("goal", prompt)),
            expected_behavior=str(raw.get("expected_behavior", prompt)),
            suite=suite,
            difficulty=difficulty,  # type: ignore[arg-type]
            initial_state=raw.get("initial_state") or {"tasks": []},
            expected_operations=list(raw.get("expected_operations") or []),
            forbidden_operations=list(raw.get("forbidden_operations") or []),
            expected_final_state=raw.get("expected_final_state"),
            metadata=dict(raw.get("metadata") or {}),
        )
    except Exception as exc:
        logger.warning("Scenario generation failed, using template: %s", exc)
        return SyntheticScenario(
            goal=prompt,
            expected_behavior=prompt,
            suite=suite,
            difficulty=difficulty,  # type: ignore[arg-type]
            initial_state={"tasks": []},
            expected_operations=[{"operation": "create_task"}] if suite == "create_task" else [],
            forbidden_operations=[{"operation": "create_task"}] if suite == "no_task" else [],
        )


async def generate_user_text(
    scenario: SyntheticScenario,
    *,
    model: str,
    current_date: str,
) -> str:
    client = LLMClient(model=model, provider="openrouter", temperature=0.8, max_tokens=500)
    user = (
        f"Date: {current_date}\nGoal: {scenario.goal}\n"
        f"Context tasks: {json.dumps(scenario.initial_state, ensure_ascii=False)}\n"
        "Write one short natural Russian user message to a PM bot. No markdown."
    )
    try:
        response = await client.complete(
            [
                Message(
                    role="system",
                    content="You write realistic user messages for PM assistant testing.",
                ),
                Message(role="user", content=user),
            ]
        )
        text = (response.content or "").strip()
        if text:
            return text
    except Exception as exc:
        logger.warning("user_text generation failed: %s", exc)
    return f"Пожалуйста, {scenario.goal.lower()}"


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def today_iso() -> str:
    return date.today().isoformat()
