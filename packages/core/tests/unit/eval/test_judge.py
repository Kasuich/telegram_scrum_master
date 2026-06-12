"""Tests for weighted LLM judge."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from core.eval.judge import (
    _parse_judge_response,
    compute_weighted_score,
    finalize_judge_evaluation,
    judge_passed,
    run_heuristic_judge,
    run_llm_judge,
)
from core.eval.schemas import EvalOperation, JudgeCriterionScore, NormalizedAgentOutput


def _criteria(**scores: float) -> dict[str, JudgeCriterionScore]:
    from core.eval.constants import JUDGE_WEIGHTS

    return {
        name: JudgeCriterionScore(
            score=scores.get(name, 8.0), weight=JUDGE_WEIGHTS[name], reason="ok"
        )
        for name in JUDGE_WEIGHTS
    }


def test_compute_weighted_score_all_tens() -> None:
    criteria = _criteria(
        action_correctness=10,
        faithfulness=10,
        intent_alignment=10,
        forbidden_compliance=10,
        completeness=10,
        final_state_quality=10,
    )
    assert compute_weighted_score(criteria) == 10.0


def test_judge_passed_requires_action_threshold() -> None:
    criteria = _criteria(action_correctness=5, intent_alignment=10)
    weighted = compute_weighted_score(criteria)
    assert weighted >= 7.0
    assert not judge_passed(weighted, criteria)


def test_judge_passed_when_both_thresholds_met() -> None:
    criteria = _criteria(action_correctness=9, intent_alignment=8)
    weighted = compute_weighted_score(criteria)
    assert judge_passed(weighted, criteria)


def test_finalize_judge_evaluation_score_scale() -> None:
    criteria = _criteria(action_correctness=8)
    result = finalize_judge_evaluation(criteria, "good")
    assert result.score == pytest.approx(result.weighted_score / 10.0)
    assert result.passed == judge_passed(result.weighted_score, criteria)


def test_heuristic_judge_create_task() -> None:
    normalized = NormalizedAgentOutput(
        operations=[EvalOperation(operation="create_task", payload={"summary": "x"})],
        final_answer="done",
    )
    result = run_heuristic_judge(
        user_text="Создай задачу",
        scenario={"suite": "create_task"},
        expected_operations=[{"operation": "create_task"}],
        forbidden_operations=[],
        normalized=normalized,
    )
    assert result.criteria["action_correctness"].score >= 7.0
    assert result.weighted_score > 0


@pytest.mark.asyncio
async def test_run_llm_judge_parses_response() -> None:
    llm_response = {
        "criteria": {
            "action_correctness": {"score": 9, "reason": "created task"},
            "faithfulness": {"score": 9, "reason": "no invented fields"},
            "intent_alignment": {"score": 8, "reason": "matches"},
            "forbidden_compliance": {"score": 10, "reason": "none"},
            "completeness": {"score": 8, "reason": "ok"},
            "final_state_quality": {"score": 7, "reason": "ok"},
        },
        "explanation": "Agent did well",
    }
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=MagicMock(content=json.dumps(llm_response)))

    with patch("core.eval.judge.LLMClient", return_value=mock_client):
        result = await run_llm_judge(
            user_text="Создай задачу",
            scenario={"suite": "create_task"},
            expected_operations=[{"operation": "create_task"}],
            forbidden_operations=[],
            initial_state={"tasks": []},
            expected_final_state=None,
            normalized=NormalizedAgentOutput(),
        )

    assert result.weighted_score >= 7.0
    assert result.passed
    assert result.criteria["action_correctness"].score == 9.0
    assert "Agent did well" in result.explanation


def test_parse_judge_response_repairs_trailing_comma() -> None:
    raw = _parse_judge_response(
        '{"criteria": {"action_correctness": {"score": 8, "reason": "ok"}}, "explanation": "fine",}'
    )
    assert raw["criteria"]["action_correctness"]["score"] == 8


@pytest.mark.asyncio
async def test_run_llm_judge_retries_on_invalid_json() -> None:
    llm_response = {
        "criteria": {
            "action_correctness": {"score": 10, "reason": "created task"},
            "faithfulness": {"score": 9, "reason": "grounded"},
            "intent_alignment": {"score": 9, "reason": "matches"},
            "forbidden_compliance": {"score": 10, "reason": "none"},
            "completeness": {"score": 9, "reason": "ok"},
            "final_state_quality": {"score": 9, "reason": "ok"},
        },
        "explanation": "Recovered",
    }
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(
        side_effect=[
            MagicMock(content='{"criteria": {broken'),
            MagicMock(content=json.dumps(llm_response)),
        ]
    )

    with patch("core.eval.judge.LLMClient", return_value=mock_client):
        result = await run_llm_judge(
            user_text="Создай задачу",
            scenario={"suite": "create_task"},
            expected_operations=[{"operation": "create_task"}],
            forbidden_operations=[],
            initial_state={"tasks": []},
            expected_final_state=None,
            normalized=NormalizedAgentOutput(
                operations=[EvalOperation(operation="create_task", payload={"summary": "x"})]
            ),
        )

    assert mock_client.complete.await_count == 2
    assert result.passed
    assert "retry" in result.explanation.lower()
    assert result.technical_error is None


@pytest.mark.asyncio
async def test_run_llm_judge_heuristic_fallback_when_json_unrecoverable() -> None:
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=MagicMock(content="not json at all"))

    with patch("core.eval.judge.LLMClient", return_value=mock_client):
        result = await run_llm_judge(
            user_text="Создай задачу",
            scenario={"suite": "create_task"},
            expected_operations=[{"operation": "create_task"}],
            forbidden_operations=[],
            initial_state={"tasks": []},
            expected_final_state=None,
            normalized=NormalizedAgentOutput(
                operations=[EvalOperation(operation="create_task", payload={"summary": "x"})]
            ),
        )

    assert mock_client.complete.await_count == 2
    assert result.criteria
    assert result.weighted_score > 0
    assert result.technical_error is None
    assert "heuristic_fallback" in (result.judge_model or "")
    assert "parse failed" in result.explanation.lower()


def test_faithfulness_gate_blocks_pass() -> None:
    """Strong everything but a hallucinated field (low faithfulness) must fail."""
    criteria = _criteria(action_correctness=9, faithfulness=3, intent_alignment=9)
    weighted = compute_weighted_score(criteria)
    assert weighted >= 7.0
    assert not judge_passed(weighted, criteria)


def _eval_all(score: float):
    return finalize_judge_evaluation(
        _criteria(
            action_correctness=score,
            faithfulness=score,
            intent_alignment=score,
            forbidden_compliance=score,
            completeness=score,
            final_state_quality=score,
        ),
        "sample",
    )


def test_aggregate_judge_samples_median_and_confidence() -> None:
    from core.eval.judge import aggregate_judge_samples

    agg = aggregate_judge_samples([_eval_all(8), _eval_all(9), _eval_all(10)], judge_model="m")
    assert agg.samples == 3
    assert agg.criteria["action_correctness"].score == 9.0  # median of 8/9/10
    assert agg.weighted_score_stddev is not None
    assert not agg.low_confidence  # tight panel


def test_aggregate_low_confidence_on_disagreement() -> None:
    from core.eval.judge import aggregate_judge_samples

    agg = aggregate_judge_samples([_eval_all(2), _eval_all(6), _eval_all(10)], judge_model="m")
    assert agg.low_confidence is True
    assert agg.confidence < 0.6


@pytest.mark.asyncio
async def test_run_llm_judge_panel_three_samples() -> None:
    from core.eval.constants import JUDGE_WEIGHTS

    responses = [
        {
            "criteria": {name: {"score": s, "reason": "r"} for name in JUDGE_WEIGHTS},
            "failure_modes": [],
            "explanation": f"sample {s}",
        }
        for s in (8, 9, 10)
    ]
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(
        side_effect=[MagicMock(content=json.dumps(r)) for r in responses]
    )

    with patch("core.eval.judge.LLMClient", return_value=mock_client):
        result = await run_llm_judge(
            user_text="Создай задачу",
            scenario={"suite": "create_task"},
            expected_operations=[{"operation": "create_task"}],
            forbidden_operations=[],
            initial_state={"tasks": []},
            expected_final_state=None,
            normalized=NormalizedAgentOutput(
                operations=[EvalOperation(operation="create_task", payload={"summary": "x"})]
            ),
            agent_trace=[{"i": 0, "kind": "tool_call", "tool": "tracker_create_issue"}],
            samples=3,
        )

    assert mock_client.complete.await_count == 3
    assert result.samples == 3
    assert result.criteria["action_correctness"].score == 9.0
    assert "Panel of 3" in result.explanation
