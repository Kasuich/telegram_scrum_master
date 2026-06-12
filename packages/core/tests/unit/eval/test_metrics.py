"""Tests for metrics aggregation."""

from __future__ import annotations

from core.eval.metrics import compute_run_metrics, percentile


def test_percentile() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95) is not None


def test_compute_run_metrics() -> None:
    rows = [
        {
            "suite": "create_task",
            "status": "completed",
            "passed": True,
            "agent_latency_sec": 10.0,
            "latency_sec": 12.0,
            "final_evaluation": {},
            "deterministic_evaluation": {},
            "llm_judge_evaluation": {
                "weighted_score": 8.0,
                "criteria": {
                    "action_correctness": {"score": 9},
                    "intent_alignment": {"score": 8},
                },
            },
        },
        {
            "suite": "no_task",
            "status": "completed",
            "passed": False,
            "agent_latency_sec": 20.0,
            "latency_sec": 22.0,
            "final_evaluation": {"errors": ["bad"]},
            "deterministic_evaluation": {"errors": [{"type": "missing_operation"}]},
            "llm_judge_evaluation": {
                "weighted_score": 4.0,
                "criteria": {"action_correctness": {"score": 3}},
            },
        },
    ]
    m = compute_run_metrics(rows)
    assert m["pass_rate"] == 0.5
    assert m["avg_agent_latency_sec"] == 15.0
    assert m["avg_weighted_score"] == 6.0
    assert m["criteria_avg"]["action_correctness"] == 6.0
    assert m["criteria_by_suite"]["create_task"]["weighted_score"] == 8.0


def test_metrics_faithfulness_confidence_cost_and_tool_latency() -> None:
    rows = [
        {
            "suite": "create_task",
            "status": "completed",
            "passed": True,
            "agent_latency_sec": 8.0,
            "latency_sec": 9.0,
            "llm_judge_evaluation": {
                "weighted_score": 9.0,
                "confidence": 0.9,
                "low_confidence": False,
                "judge_model": "google/gemini-3.1-pro-preview",
                "judge_prompt_tokens": 2000,
                "judge_completion_tokens": 400,
                "criteria": {"faithfulness": {"score": 9.0}},
            },
            "tool_latency": {"by_op": {"create_issue": {"count": 1, "total_sec": 0.5}}},
        },
        {
            "suite": "create_task",
            "status": "completed",
            "passed": False,
            "agent_latency_sec": 12.0,
            "latency_sec": 13.0,
            "llm_judge_evaluation": {
                "weighted_score": 4.0,
                "confidence": 0.4,
                "low_confidence": True,
                "judge_model": "heuristic",
                "criteria": {"faithfulness": {"score": 3.0}},  # < gate → hallucination
            },
            "tool_latency": {"by_op": {"search": {"count": 2, "total_sec": 1.2}}},
        },
    ]
    m = compute_run_metrics(rows, judge_model="google/gemini-3.1-pro-preview")
    assert m["faithfulness_avg"] == 6.0
    assert m["hallucination_rate"] == 0.5  # one case below the faithfulness gate
    assert m["avg_judge_confidence"] == 0.65
    assert m["low_confidence_rate"] == 0.5
    assert m["judge_trust"] == {"llm_judged": 1, "heuristic_judged": 1}
    # 2000 prompt + 400 completion on pro pricing (2/12 per 1M)
    assert m["judge_cost_usd"] is not None and m["judge_cost_usd"] > 0
    assert m["tool_latency_by_op"]["search"]["calls"] == 2
    assert m["total_tool_latency_sec"] == 1.7
