"""Tests for deterministic evaluator."""

from __future__ import annotations

from core.eval.deterministic import evaluate_deterministic
from core.eval.schemas import EvalOperation, NormalizedAgentOutput


def test_forbidden_create_detected() -> None:
    normalized = NormalizedAgentOutput(
        operations=[EvalOperation(operation="create_task", payload={"summary": "x"})]
    )
    result = evaluate_deterministic(
        normalized,
        expected_operations=[],
        forbidden_operations=[{"operation": "create_task"}],
    )
    assert not result.passed
    assert any(e["type"] == "forbidden_operation_executed" for e in result.errors)
