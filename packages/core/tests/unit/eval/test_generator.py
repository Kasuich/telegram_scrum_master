"""Tests for scenario generation — DARKHORSE queue enforcement."""

from __future__ import annotations

import pytest
from core.eval.constants import EVAL_QUEUE
from core.eval.generator import _force_queue, _swap_queue_key, generate_scenario
from core.eval.schemas import SyntheticScenario


def test_swap_queue_key_preserves_number() -> None:
    assert _swap_queue_key("SUPPORT-10", "DARKHORSE") == "DARKHORSE-10"
    assert _swap_queue_key("TEST-3", "DARKHORSE") == "DARKHORSE-3"
    assert _swap_queue_key("DARKHORSE-7", "DARKHORSE") == "DARKHORSE-7"
    assert _swap_queue_key("not-a-key", "DARKHORSE") == "not-a-key"


def test_force_queue_rewrites_keys_and_references() -> None:
    scenario = SyntheticScenario(
        goal="x",
        expected_behavior="x",
        suite="update_task",
        initial_state={"queue": "SUPPORT", "tasks": [{"key": "SUPPORT-10", "summary": "a"}]},
        expected_operations=[{"operation": "update_task", "match": {"task_key": "SUPPORT-10"}}],
        forbidden_operations=[{"operation": "create_task"}],
    )
    forced = _force_queue(scenario, EVAL_QUEUE)
    assert forced.initial_state["queue"] == "DARKHORSE"
    assert forced.initial_state["tasks"][0]["key"] == "DARKHORSE-10"
    # The cross-reference in expected_operations is rewritten consistently.
    assert forced.expected_operations[0]["match"]["task_key"] == "DARKHORSE-10"


@pytest.mark.asyncio
async def test_generate_scenario_fixture_uses_eval_queue() -> None:
    # update_task fixture (index 1) carries a seeded task key — must be DARKHORSE.
    scenario = await generate_scenario(
        suite="update_task", difficulty="medium", current_date="2026-06-12", model="x", index=1
    )
    keys = [t.get("key") for t in scenario.initial_state.get("tasks", [])]
    assert keys and all(k.startswith("DARKHORSE-") for k in keys)
