"""LLM-as-a-judge with weighted 0-10 criteria."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from core.eval.constants import (
    DEFAULT_JUDGE_MODEL,
    JUDGE_CRITERION_WARN_MIN,
    JUDGE_PARSE_MAX_ATTEMPTS,
    JUDGE_PASS_ACTION_MIN,
    JUDGE_PASS_WEIGHTED_MIN,
    JUDGE_WEIGHTS,
)
from core.eval.schemas import JudgeCriterionScore, LLMJudgeEvaluation, NormalizedAgentOutput
from core.llm import LLMClient, Message

logger = logging.getLogger(__name__)

_CRITERION_LABELS = {
    "action_correctness": "Correct action type (create/update/search/noop)",
    "intent_alignment": "Result matches user_text intent",
    "forbidden_compliance": "No forbidden operations",
    "completeness": "All required steps performed",
    "final_state_quality": "Tracker state is reasonable",
}

_JUDGE_SYSTEM = """You are an eval judge for a PM agent working with Yandex Tracker.
Compare expected JSON (scenario + user message + expectations) with actual JSON (agent operations + tracker state).

Score each criterion 0-10 (integers or one decimal). The MOST IMPORTANT criterion is action_correctness:
if the user needed a task created and the agent created a task, score action_correctness high even if
summary wording differs from fixture expectations. Judge intent against user_text, not rigid fixture tokens.

Tracker domain rules (do NOT penalize correct agent behavior):
- Valid priority keys on the board: blocker, critical, normal, minor, trivial. There is NO "high" or "low".
- If user_text or fixture says "high" / "высокий" and agent used priority "critical" — that is CORRECT; score
  intent_alignment and final_state_quality 9-10, not lower.
- Similarly "low" maps to minor/trivial. Minor wording differences in summary/description/URL are OK if
  the semantic intent is preserved.
- "noop" in actual.operations means the agent made NO tracker write calls — only a text reply. That is fine
  for no_task suite; for create/update suites it means the agent failed to act.

Criteria:
- action_correctness (weight 0.40): right TYPE of action for the suite/intent
- intent_alignment (weight 0.25): summary/description/comments match user_text
- forbidden_compliance (weight 0.15): no forbidden operations executed
- completeness (weight 0.10): correct number of steps, nothing critical missing
- final_state_quality (weight 0.10): fake tracker state is sensible

Return strict JSON only:
{
  "criteria": {
    "action_correctness": {"score": 0-10, "reason": "..."},
    "intent_alignment": {"score": 0-10, "reason": "..."},
    "forbidden_compliance": {"score": 0-10, "reason": "..."},
    "completeness": {"score": 0-10, "reason": "..."},
    "final_state_quality": {"score": 0-10, "reason": "..."}
  },
  "explanation": "one paragraph summary"
}
Do NOT compute weighted_score yourself."""

_JUDGE_JSON_RETRY_HINT = (
    "Your previous response was not valid JSON. "
    "Return ONLY one JSON object with keys criteria and explanation. No markdown."
)


def compute_weighted_score(
    criteria: dict[str, JudgeCriterionScore],
    weights: dict[str, float] | None = None,
) -> float:
    w = weights or JUDGE_WEIGHTS
    total = 0.0
    for name, weight in w.items():
        criterion = criteria.get(name)
        if criterion is None:
            continue
        total += criterion.score * weight
    return round(total, 2)


def judge_passed(weighted_score: float, criteria: dict[str, JudgeCriterionScore]) -> bool:
    action = criteria.get("action_correctness")
    action_score = action.score if action else 0.0
    return weighted_score >= JUDGE_PASS_WEIGHTED_MIN and action_score >= JUDGE_PASS_ACTION_MIN


def build_judge_errors(criteria: dict[str, JudgeCriterionScore], explanation: str) -> list[str]:
    errors: list[str] = []
    for name, criterion in criteria.items():
        if criterion.score < JUDGE_CRITERION_WARN_MIN:
            label = _CRITERION_LABELS.get(name, name)
            errors.append(f"{label}: {criterion.score}/10 — {criterion.reason}")
    if explanation and not errors:
        pass
    elif explanation:
        errors.append(explanation)
    return errors


def _parse_criteria(raw: dict[str, Any]) -> dict[str, JudgeCriterionScore]:
    criteria: dict[str, JudgeCriterionScore] = {}
    raw_criteria = raw.get("criteria") or {}
    for name, weight in JUDGE_WEIGHTS.items():
        item = raw_criteria.get(name) or {}
        score = float(item.get("score", 0.0))
        score = max(0.0, min(10.0, score))
        criteria[name] = JudgeCriterionScore(
            score=score,
            weight=weight,
            reason=str(item.get("reason", "")),
        )
    return criteria


def finalize_judge_evaluation(
    criteria: dict[str, JudgeCriterionScore],
    explanation: str,
    *,
    judge_model: str | None = None,
    technical_error: str | None = None,
) -> LLMJudgeEvaluation:
    weighted = compute_weighted_score(criteria)
    passed = judge_passed(weighted, criteria)
    return LLMJudgeEvaluation(
        criteria=criteria,
        weighted_score=weighted,
        passed=passed,
        score=round(weighted / 10.0, 4),
        explanation=explanation,
        judge_model=judge_model,
        technical_error=technical_error,
    )


def _repair_json(text: str) -> str:
    """Best-effort fixes for common LLM JSON mistakes."""
    text = (
        text.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)
    return text


def _parse_judge_response(content: str) -> dict[str, Any]:
    text = _extract_json(content)
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raw = json.loads(_repair_json(text))
    if not isinstance(raw, dict) or not isinstance(raw.get("criteria"), dict):
        raise ValueError("judge response missing criteria object")
    return raw


async def run_llm_judge(
    *,
    user_text: str,
    scenario: dict[str, Any],
    expected_operations: list[dict[str, Any]] | None,
    forbidden_operations: list[dict[str, Any]] | None,
    initial_state: dict[str, Any] | None,
    expected_final_state: dict[str, Any] | None,
    normalized: NormalizedAgentOutput,
    final_fake_tracker_state: dict[str, Any] | None = None,
    model: str = DEFAULT_JUDGE_MODEL,
) -> LLMJudgeEvaluation:
    client = LLMClient(model=model, provider="openrouter", temperature=0.0, max_tokens=1200)
    payload = {
        "expected": {
            "user_text": user_text,
            "scenario": scenario,
            "expected_operations": expected_operations or [],
            "forbidden_operations": forbidden_operations or [],
            "initial_state": initial_state or {},
            "expected_final_state": expected_final_state,
        },
        "actual": {
            "operations": [op.model_dump() for op in normalized.operations],
            "final_fake_tracker_state": final_fake_tracker_state,
            "final_answer": normalized.final_answer,
        },
    }
    messages: list[Message] = [
        Message(role="system", content=_JUDGE_SYSTEM),
        Message(role="user", content=json.dumps(payload, ensure_ascii=False)),
    ]
    last_error: Exception | None = None
    last_content = ""

    for attempt in range(JUDGE_PARSE_MAX_ATTEMPTS):
        try:
            response = await client.complete(messages)
            last_content = response.content or ""
            raw = _parse_judge_response(last_content)
            criteria = _parse_criteria(raw)
            explanation = str(raw.get("explanation", ""))
            if attempt > 0:
                explanation = f"[Judge JSON recovered on retry] {explanation}".strip()
            return finalize_judge_evaluation(
                criteria,
                explanation,
                judge_model=model,
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                "LLM judge parse attempt %s/%s failed: %s",
                attempt + 1,
                JUDGE_PARSE_MAX_ATTEMPTS,
                exc,
            )
            if attempt + 1 < JUDGE_PARSE_MAX_ATTEMPTS:
                messages = [
                    *messages,
                    Message(role="assistant", content=last_content),
                    Message(role="user", content=_JUDGE_JSON_RETRY_HINT),
                ]

    logger.warning("LLM judge falling back to heuristic after parse failure: %s", last_error)
    heuristic = run_heuristic_judge(
        user_text=user_text,
        scenario=scenario,
        expected_operations=expected_operations,
        forbidden_operations=forbidden_operations,
        normalized=normalized,
        final_fake_tracker_state=final_fake_tracker_state,
    )
    note = f"Heuristic fallback (LLM judge JSON parse failed: {last_error})"
    explanation = f"{note}. {heuristic.explanation}".strip()
    return heuristic.model_copy(
        update={
            "explanation": explanation,
            "judge_model": f"{model}+heuristic_fallback",
        }
    )


def run_heuristic_judge(
    *,
    user_text: str,
    scenario: dict[str, Any],
    expected_operations: list[dict[str, Any]] | None,
    forbidden_operations: list[dict[str, Any]] | None,
    normalized: NormalizedAgentOutput,
    final_fake_tracker_state: dict[str, Any] | None = None,
) -> LLMJudgeEvaluation:
    """Fast fallback when use_llm_judge=false — same schema as LLM judge."""
    del user_text, final_fake_tracker_state
    suite = str(scenario.get("suite") or "")
    op_types = [op.operation for op in normalized.operations]
    forbidden_names = {
        f.get("operation") for f in (forbidden_operations or []) if f.get("operation")
    }
    expected_names = [e.get("operation") for e in (expected_operations or []) if e.get("operation")]

    forbidden_hit = any(op in forbidden_names for op in op_types)
    forbidden_score = 10.0 if not forbidden_hit else 0.0

    if suite == "no_task":
        action_score = 10.0 if not op_types else 2.0
    elif suite == "create_task":
        action_score = 10.0 if "create_task" in op_types else 1.0
    elif suite == "update_task":
        action_score = 10.0 if "update_task" in op_types and "create_task" not in op_types else 3.0
    elif suite == "duplicate_search":
        has_search = "search_tasks" in op_types
        no_create = "create_task" not in op_types
        action_score = 9.0 if has_search and no_create else 4.0
    else:
        action_score = 7.0 if op_types else 4.0

    expected_set = set(expected_names)
    actual_set = set(op_types)
    if expected_set and expected_set <= actual_set:
        completeness_score = 9.0
    elif expected_set and actual_set & expected_set:
        completeness_score = 6.0
    elif not expected_set and not op_types:
        completeness_score = 9.0
    else:
        completeness_score = 4.0

    intent_score = 8.0 if action_score >= 7.0 else 4.0
    state_score = 7.0 if action_score >= 6.0 else 3.0

    criteria: dict[str, JudgeCriterionScore] = {}
    scores = {
        "action_correctness": (action_score, "Heuristic: action type vs suite"),
        "intent_alignment": (intent_score, "Heuristic: inferred from action match"),
        "forbidden_compliance": (forbidden_score, "Heuristic: forbidden ops check"),
        "completeness": (completeness_score, "Heuristic: expected ops coverage"),
        "final_state_quality": (state_score, "Heuristic: assumed OK if action matched"),
    }
    for name, (score, reason) in scores.items():
        criteria[name] = JudgeCriterionScore(
            score=score,
            weight=JUDGE_WEIGHTS[name],
            reason=reason,
        )

    return finalize_judge_evaluation(
        criteria,
        "Heuristic judge (use_llm_judge=false)",
        judge_model="heuristic",
    )


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        text = "\n".join(lines)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text
