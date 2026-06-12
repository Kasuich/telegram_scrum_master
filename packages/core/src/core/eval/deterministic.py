"""Deterministic eval checks."""

from __future__ import annotations

from typing import Any

from core.eval.schemas import DeterministicEvaluation, NormalizedAgentOutput


def _op_types(output: NormalizedAgentOutput) -> list[str]:
    return [op.operation for op in output.operations]


def _find_ops(output: NormalizedAgentOutput, operation: str) -> list[Any]:
    return [op for op in output.operations if op.operation == operation]


def _match_operation(op: Any, match: dict[str, Any]) -> bool:
    if "task_key" in match:
        if (op.task_key or "").upper() != str(match["task_key"]).upper():
            return False
    if "query_should_contain" in match:
        query = (op.query or "").lower()
        for token in match["query_should_contain"]:
            if str(token).lower() not in query:
                return False
    payload = op.payload or {}
    if "summary_should_contain" in match:
        summary = str(payload.get("summary", "")).lower()
        for token in match["summary_should_contain"]:
            if str(token).lower() not in summary:
                return False
    if "assignee" in match:
        if str(payload.get("assignee", "")).lower() != str(match["assignee"]).lower():
            return False
    if "priority" in match:
        if str(payload.get("priority", "")).lower() != str(match["priority"]).lower():
            return False
    if "queue" in match:
        if str(payload.get("queue", "")).upper() != str(match["queue"]).upper():
            return False
    return True


def evaluate_deterministic(
    normalized: NormalizedAgentOutput,
    expected_operations: list[dict[str, Any]] | None,
    forbidden_operations: list[dict[str, Any]] | None,
) -> DeterministicEvaluation:
    errors: list[dict[str, Any]] = []
    types = _op_types(normalized)

    for forbidden in forbidden_operations or []:
        op_name = forbidden.get("operation")
        if op_name and op_name in types:
            errors.append({"type": "forbidden_operation_executed", "operation": op_name})

    for expected in expected_operations or []:
        op_name = expected.get("operation")
        if not op_name:
            continue
        candidates = _find_ops(normalized, op_name)
        match = expected.get("match") or {}
        if not candidates:
            errors.append({"type": "missing_operation", "operation": op_name})
            continue
        if match and not any(_match_operation(c, match) for c in candidates):
            errors.append({"type": "operation_mismatch", "operation": op_name, "match": match})

    create_count = types.count("create_task")
    if expected_operations:
        expected_creates = sum(
            1 for e in expected_operations if e.get("operation") == "create_task"
        )
        if expected_creates and create_count != expected_creates:
            errors.append(
                {
                    "type": "create_count_mismatch",
                    "expected": expected_creates,
                    "actual": create_count,
                }
            )

    return DeterministicEvaluation(passed=not errors, errors=errors)
