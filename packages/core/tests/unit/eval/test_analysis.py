"""Tests for run-level failure analysis."""

from __future__ import annotations

from core.eval.analysis import aggregate_failure_modes, build_failure_analysis, weak_spots


def _rows() -> list[dict]:
    return [
        {
            "status": "completed",
            "passed": False,
            "suite": "duplicate_search",
            "difficulty": "medium",
            "llm_judge_evaluation": {
                "failure_modes": ["over_creation", "missed_search"],
                "judge_model": "google/gemini-3.1-pro-preview",
                "low_confidence": True,
                "criteria": {"action_correctness": {"score": 3}},
            },
            "deterministic_evaluation": {
                "errors": [{"type": "forbidden_operation_executed", "operation": "create_task"}]
            },
        },
        {
            "status": "completed",
            "passed": False,
            "suite": "create_task",
            "difficulty": "easy",
            "llm_judge_evaluation": {
                "failure_modes": ["no_action_when_needed"],
                "judge_model": "heuristic",
            },
            "deterministic_evaluation": {"errors": []},
        },
        {
            "status": "completed",
            "passed": True,
            "suite": "create_task",
            "difficulty": "easy",
            "llm_judge_evaluation": {
                "failure_modes": [],
                "judge_model": "google/gemini-3.1-pro-preview",
            },
        },
    ]


def test_aggregate_failure_modes_counts_judge_and_deterministic() -> None:
    modes = {m["mode"]: m["count"] for m in aggregate_failure_modes(_rows())}
    assert modes["over_creation"] == 1
    assert modes["missed_search"] == 1
    assert modes["forbidden_operation"] == 1  # mapped from deterministic error
    assert modes["no_action_when_needed"] == 1
    assert "passed" not in modes  # passing cases contribute nothing


def test_weak_spots_flags_low_suites_and_criteria() -> None:
    metrics = {
        "suite_stats": {
            "duplicate_search": {"pass_rate": 0.2, "n": 5},
            "create_task": {"pass_rate": 0.9, "n": 10},
        },
        "criteria_avg": {"action_correctness": 4.5, "faithfulness": 8.1},
    }
    spots = weak_spots(metrics)
    kinds = {(s["kind"], s["name"]) for s in spots}
    assert ("suite", "duplicate_search") in kinds
    assert ("criterion", "action_correctness") in kinds
    assert ("suite", "create_task") not in kinds  # 0.9 is healthy
    assert ("criterion", "faithfulness") not in kinds  # 8.1 is healthy


def test_build_failure_analysis_counts() -> None:
    analysis = build_failure_analysis(_rows(), {"suite_stats": {}, "criteria_avg": {}})
    assert analysis["failed_count"] == 2
    assert analysis["low_confidence_count"] == 1
    assert analysis["heuristic_judged_count"] == 1
    assert analysis["failure_modes"]  # non-empty
