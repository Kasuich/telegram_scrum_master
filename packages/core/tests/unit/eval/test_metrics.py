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
