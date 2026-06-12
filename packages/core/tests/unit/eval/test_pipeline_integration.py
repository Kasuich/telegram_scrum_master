"""Integration test for batch pipeline with mocked agent RPC."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from core.eval.pipeline.base import PipelineContext
from core.eval.pipeline.batch import BatchStagesPipeline
from core.eval.schemas import EvalRunConfig
from core.react import AgentResult


@pytest.mark.asyncio
async def test_batch_pipeline_single_case_end_to_end() -> None:
    run_id = uuid.uuid4()
    case_id = uuid.uuid4()

    repo = AsyncMock()
    repo.set_run_status = AsyncMock()
    repo.refresh_run_progress = AsyncMock()
    repo.create_case = AsyncMock(
        return_value=SimpleNamespace(id=case_id, generated_scenario_json=None, user_text=None)
    )
    repo.update_case = AsyncMock()
    repo.update_case_result = AsyncMock()
    repo.collect_case_metric_rows = AsyncMock(return_value=[])
    repo.save_metrics = AsyncMock()
    repo.update_run_counters = AsyncMock()
    repo.log_event = AsyncMock()

    case_detail = SimpleNamespace(
        id=case_id,
        user_text="Создай задачу про баг",
        generated_scenario_json={
            "goal": "create",
            "expected_behavior": "create one task",
            "suite": "create_task",
            "difficulty": "easy",
            "initial_state": {"tasks": []},
            "expected_operations": [{"operation": "create_task"}],
            "forbidden_operations": [],
            "metadata": {},
        },
        initial_state_json={"tasks": []},
        expected_operations_json=[{"operation": "create_task"}],
        forbidden_operations_json=[],
        current_date="2026-06-12",
        status="user_text_generated",
        started_at=datetime.now(timezone.utc),
        expected_final_state_json=None,
        result=SimpleNamespace(
            agent_latency_sec=1.5,
            agent_normalized_output_json={
                "operations": [{"operation": "create_task", "payload": {"summary": "Баг"}}],
                "final_answer": "Создал TEST-1",
            },
            final_fake_tracker_state_json={"tasks": [{"key": "TEST-1"}]},
            final_evaluation_json=None,
            deterministic_evaluation_json=None,
            passed=None,
            score=None,
        ),
    )

    async def get_case_detail(rid: uuid.UUID, cid: uuid.UUID):
        del rid, cid
        return case_detail

    repo.get_case_detail = AsyncMock(side_effect=get_case_detail)

    agent_result = AgentResult(
        reply="Создал TEST-1",
        session_id=f"eval-{case_id}",
        steps=[
            {
                "kind": "tool_call",
                "tool_name": "tracker_create_issue",
                "arguments": {"summary": "Баг"},
            },
            {
                "kind": "tool_result",
                "tool_name": "tracker_create_issue",
                "result": {"key": "TEST-1"},
            },
        ],
        eval_artifacts={"final_fake_tracker_state": {"tasks": [{"key": "TEST-1"}]}},
    )

    rpc = AsyncMock()
    rpc.invoke_agent = AsyncMock(return_value=agent_result)

    config = EvalRunConfig(
        n_cases=1,
        suites=["create_task"],
        scenario_generation_concurrency=1,
        user_text_generation_concurrency=1,
        agent_concurrency=1,
        judge_concurrency=1,
        use_llm_judge=False,
    )

    ctx = PipelineContext(run_id=run_id, config=config, repo=repo, rpc=rpc)

    with (
        patch("core.eval.pipeline.batch.generate_scenario", AsyncMock()) as gen_scenario,
        patch(
            "core.eval.pipeline.batch.generate_user_text", AsyncMock(return_value="Создай задачу")
        ) as gen_text,
    ):
        from core.eval.schemas import SyntheticScenario

        gen_scenario.return_value = SyntheticScenario(
            goal="create",
            expected_behavior="create",
            suite="create_task",
            difficulty="easy",
            initial_state={"tasks": []},
            expected_operations=[{"operation": "create_task"}],
        )
        await BatchStagesPipeline().run(ctx)

    rpc.invoke_agent.assert_awaited()
    gen_text.assert_awaited()
    repo.update_case_result.assert_awaited()
    repo.set_run_status.assert_any_await(run_id, "completed")

    judge_calls = [
        c for c in repo.update_case_result.await_args_list if c.kwargs.get("passed") is not None
    ]
    assert judge_calls, "judge should persist passed/score"
    assert judge_calls[-1].kwargs["passed"] is True
    assert (judge_calls[-1].kwargs.get("score") or 0) > 0
