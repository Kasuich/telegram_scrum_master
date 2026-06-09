from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.goal import GoalItem, GoalPlan
from core.stage_graph import StageId
from core.react import _goal_met, GoalVerdict, _goal_terminal_for_stage


def test_goal_verdict_dataclass():
    v = GoalVerdict(met=True, reason="issue_created", tier=1)
    assert v.met is True
    assert v.reason == "issue_created"
    assert v.tier == 1


@pytest.mark.asyncio
async def test_goal_met_tier1_intake_created():
    goal = GoalItem(stage=StageId.INTAKE, payload="create task", intent="create")
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_create_issue",
            "result": {"key": "TEST-42", "summary": "task"},
        }
    ]
    verdict = await _goal_met(goal, steps)
    assert verdict.met is True
    assert verdict.tier == 1


@pytest.mark.asyncio
async def test_goal_met_tier1_intake_no_create():
    goal = GoalItem(stage=StageId.INTAKE, payload="create task", intent="create")
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_find_issues",
            "result": {"count": 0},
        }
    ]
    verdict = await _goal_met(goal, steps)
    assert verdict.met is False


@pytest.mark.asyncio
async def test_goal_met_tier1_status_comment_succeeded():
    goal = GoalItem(stage=StageId.STATUS, payload="status update", intent="comment")
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_comment_issue",
            "result": {"issue_key": "TEST-1", "text": "status update"},
        }
    ]
    verdict = await _goal_met(goal, steps)
    assert verdict.met is True
    assert verdict.tier == 1
    assert verdict.reason == "comment_succeeded"


@pytest.mark.asyncio
async def test_goal_met_tier1_transition_close_succeeded():
    goal = GoalItem(stage=StageId.TRANSITION, payload="close task", intent="close")
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_close_issue",
            "result": {"issue": {"key": "TEST-1", "status": "Закрыт"}},
        }
    ]
    verdict = await _goal_met(goal, steps)
    assert verdict.met is True
    assert verdict.tier == 1
    assert verdict.reason == "transition_succeeded"


@pytest.mark.asyncio
async def test_goal_met_tier1_query_data_present_with_entities():
    goal = GoalItem(
        stage=StageId.QUERY,
        payload="find my tasks",
        intent="query",
        entities={"assignee": "me"},
    )
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_find_issues",
            "result": {"count": 3, "issues": []},
        }
    ]
    verdict = await _goal_met(goal, steps)
    assert verdict.met is True
    assert verdict.tier == 1
    assert verdict.reason == "query_data_present"


@pytest.mark.asyncio
async def test_goal_met_tier1_query_data_present_no_entities():
    goal = GoalItem(
        stage=StageId.QUERY,
        payload="show board",
        intent="query",
        entities={},
    )
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_board_snapshot",
            "result": {"columns": 5},
        }
    ]
    verdict = await _goal_met(goal, steps)
    assert verdict.met is True
    assert verdict.tier == 1
    assert verdict.reason == "query_data_present"


@pytest.mark.asyncio
async def test_goal_met_tier1_query_no_data():
    goal = GoalItem(stage=StageId.QUERY, payload="find tasks", intent="query")
    steps = []
    verdict = await _goal_met(goal, steps)
    assert verdict.met is False
    assert verdict.reason == "no_data"
    assert verdict.tier == 1


@pytest.mark.asyncio
async def test_goal_met_tier2_llm_judge_yes():
    goal = GoalItem(
        stage=StageId.REORG,
        payload="reassign task",
        intent="reorg",
        success_criteria="get data",
    )
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_find_issues",
            "result": {"count": 0},
        }
    ]

    mock_resp = MagicMock()
    mock_resp.content = "YES"
    mock_client = AsyncMock()
    mock_client.complete = AsyncMock(return_value=mock_resp)
    mock_client.close = AsyncMock()

    with patch("core.react.LLMClient", return_value=mock_client):
        verdict = await _goal_met(goal, steps, use_llm=True)
    assert verdict.met is True
    assert verdict.tier == 2


@pytest.mark.asyncio
async def test_goal_met_tier2_llm_judge_no():
    goal = GoalItem(
        stage=StageId.REORG,
        payload="reassign task",
        intent="reorg",
        success_criteria="reassign done",
    )
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_find_issues",
            "result": {"count": 0},
        }
    ]

    mock_resp = MagicMock()
    mock_resp.content = "NO|missing data"
    mock_client = AsyncMock()
    mock_client.complete = AsyncMock(return_value=mock_resp)
    mock_client.close = AsyncMock()

    with patch("core.react.LLMClient", return_value=mock_client):
        verdict = await _goal_met(goal, steps, use_llm=True)
    assert verdict.met is False
    assert verdict.tier == 2


@pytest.mark.asyncio
async def test_goal_met_tier2_llm_judge_needs_more():
    goal = GoalItem(
        stage=StageId.REORG,
        payload="change sprint",
        intent="reorg",
        success_criteria="sprint assigned",
    )
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_find_issues",
            "result": {"count": 1},
        }
    ]

    mock_resp = MagicMock()
    mock_resp.content = "NEEDS_MORE|need sprint name"
    mock_client = AsyncMock()
    mock_client.complete = AsyncMock(return_value=mock_resp)
    mock_client.close = AsyncMock()

    with patch("core.react.LLMClient", return_value=mock_client):
        verdict = await _goal_met(goal, steps, use_llm=True)
    assert verdict.met is False
    assert verdict.tier == 2


@pytest.mark.asyncio
async def test_goal_met_tier2_llm_failure_falls_back():
    goal = GoalItem(
        stage=StageId.REORG,
        payload="change priority",
        intent="reorg",
        success_criteria="priority updated",
    )
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_find_issues",
            "result": {"count": 1},
        }
    ]

    mock_client = AsyncMock()
    mock_client.complete = AsyncMock(side_effect=Exception("LLM down"))
    mock_client.close = AsyncMock()

    with patch("core.react.LLMClient", return_value=mock_client):
        verdict = await _goal_met(goal, steps, use_llm=True)
    assert verdict.met is False
    assert verdict.reason == "judge_error"
    assert verdict.tier == 2


@pytest.mark.asyncio
async def test_goal_met_no_llm_flag():
    goal = GoalItem(stage=StageId.REORG, payload="reassign", intent="reorg")
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_find_issues",
            "result": {"count": 0},
        }
    ]
    verdict = await _goal_met(goal, steps, use_llm=False)
    assert verdict.met is False
    assert verdict.tier == 1


def test_goal_terminal_for_stage_query_with_data():
    stage = __import__("core.stage_graph", fromlist=["QUERY"]).QUERY
    goal = GoalItem(stage=StageId.QUERY, payload="find", intent="query", entities={"q": "x"})
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_find_issues",
            "result": {"count": 2},
        }
    ]
    assert _goal_terminal_for_stage(stage, goal, steps) is True


def test_goal_terminal_for_stage_query_no_data():
    stage = __import__("core.stage_graph", fromlist=["QUERY"]).QUERY
    goal = GoalItem(stage=StageId.QUERY, payload="find", intent="query")
    assert _goal_terminal_for_stage(stage, goal, []) is False


def test_goal_terminal_for_stage_query_error_result():
    stage = __import__("core.stage_graph", fromlist=["QUERY"]).QUERY
    goal = GoalItem(stage=StageId.QUERY, payload="find", intent="query")
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_find_issues",
            "result": {"error": "not found"},
        }
    ]
    assert _goal_terminal_for_stage(stage, goal, steps) is False


def test_goal_terminal_for_stage_non_query():
    from core.stage_graph import INTAKE as intake_stage

    goal = GoalItem(stage=StageId.INTAKE, payload="create", intent="create")
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_create_issue",
            "result": {"key": "TEST-1"},
        }
    ]
    assert _goal_terminal_for_stage(intake_stage, goal, steps) is True


def test_goal_terminal_for_stage_none():
    goal = GoalItem(stage=StageId.QUERY, payload="find", intent="query")
    assert _goal_terminal_for_stage(None, goal, []) is False


@pytest.mark.asyncio
async def test_goal_met_query_no_metric_tier1():
    goal = GoalItem(
        stage=StageId.QUERY,
        payload="find my tasks",
        intent="query",
        entities={"assignee": "me"},
    )
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_find_issues",
            "result": {"count": 3, "issues": []},
        }
    ]
    verdict = await _goal_met(goal, steps, use_llm=True)
    assert verdict.met is True
    assert verdict.tier == 1
    assert verdict.reason == "query_data_present"


@pytest.mark.asyncio
async def test_goal_met_query_with_metric_falls_to_judge():
    goal = GoalItem(
        stage=StageId.QUERY,
        payload="Сколько SP у Коли?",
        intent="query",
        entities={"metric": "story_points", "assignee": "kolya"},
        success_criteria="Указано количество story points",
    )
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_board_snapshot",
            "result": {"total": 5, "by_assignee_sp": {"Коля": 8}},
        }
    ]

    mock_resp = MagicMock()
    mock_resp.content = "YES"
    mock_client = AsyncMock()
    mock_client.complete = AsyncMock(return_value=mock_resp)
    mock_client.close = AsyncMock()

    with patch("core.react.LLMClient", return_value=mock_client):
        verdict = await _goal_met(goal, steps, use_llm=True)
    assert verdict.met is True
    assert verdict.tier == 2


@pytest.mark.asyncio
async def test_goal_met_query_with_metric_no_llm_fallback():
    goal = GoalItem(
        stage=StageId.QUERY,
        payload="Сколько SP у Коли?",
        intent="query",
        entities={"metric": "story_points", "assignee": "kolya"},
    )
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_board_snapshot",
            "result": {"total": 5},
        }
    ]
    verdict = await _goal_met(goal, steps, use_llm=False)
    assert verdict.met is True
    assert verdict.tier == 1
    assert verdict.reason == "query_data_present_no_llm"


@pytest.mark.asyncio
async def test_goal_met_query_metric_no_data():
    goal = GoalItem(
        stage=StageId.QUERY,
        payload="Сколько SP у Коли?",
        intent="query",
        entities={"metric": "story_points"},
    )
    steps = []
    verdict = await _goal_met(goal, steps, use_llm=False)
    assert verdict.met is False
    assert verdict.tier == 1
