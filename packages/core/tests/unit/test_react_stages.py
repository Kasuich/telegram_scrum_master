"""Stage-graph integration tests for ReActRunner (fake LLM, in-memory store).

These verify the graph drives the action-only PM agent: stage is frozen per
turn, whitelists reject off-stage tools, forced edges auto-chain without an
extra LLM call, and terminal nodes end the turn.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from core.agent import BaseAgent, LLMSettings
from core.config import RuntimeConfig
from core.react import ReActRunner
from core.tools import ToolRegistry, platform_tool

ENV = {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test_db",
    "YC_API_KEY": "test_api_key_12345678901234567890",
    "YC_FOLDER_ID": "b1g1234567890abcdef",
    "TRACKER_TOKEN": "test_tracker_token_12345678901234567890",
    "TRACKER_ORG_ID": "12345678901234567890",
}


def _text_response(text: str) -> dict[str, Any]:
    return {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "output_text": text,
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        "status": "completed",
    }


def _tool_call_response(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "output": [
            {
                "type": "function_call",
                "call_id": "fc_1",
                "name": name,
                "arguments": json.dumps(args),
            }
        ],
        "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
        "status": "completed",
    }


def _http_ok(data: dict[str, Any]) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = data
    resp.text = json.dumps(data)
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture(autouse=True)
def _clean_registry():
    ToolRegistry().clear()
    yield
    ToolRegistry().clear()


@pytest.fixture(autouse=True)
def _rules_only_turn_plan():
    from core.goal import GoalPlan

    async def _fake_plan(message: str, *, use_llm: bool = True):
        from core.stage_graph import StageId
        from core.stage_router import detect_stage_rules

        sid = detect_stage_rules(message) or StageId.QUERY
        return GoalPlan.single(sid, message)

    with patch("core.react.build_goal_plan", side_effect=_fake_plan):
        yield


def _register_fake_tracker_tools():
    """Register stand-ins for the real tracker tools the stages whitelist."""

    @platform_tool(name="tracker_find_issues", risk="low", scopes=["tracker:read"])
    async def tracker_find_issues(summary_hint: str = "", assignee: str = "") -> dict:
        "find"
        return {"count": 1, "issues": [{"key": "DARKHORSE-1", "summary": "x"}]}

    @platform_tool(name="tracker_board_snapshot", risk="low", scopes=["tracker:read"])
    async def tracker_board_snapshot(queue: str = "") -> dict:
        "snapshot"
        return {"total": 3, "by_status": {"Open": 3}, "overdue": []}

    @platform_tool(name="backlog_plan", risk="low", scopes=["tracker:read"])
    async def backlog_plan(text: str = "") -> dict:
        "plan"
        return {"plan": {"epic": {}}, "tasks_count": 4, "stories_count": 2}

    @platform_tool(name="tracker_apply_backlog_plan", risk="medium", scopes=["tracker:write"])
    async def tracker_apply_backlog_plan(plan_json: str = "") -> dict:
        "apply"
        return {"created_count": 6, "epic_key": "DARKHORSE-10"}

    @platform_tool(name="tracker_create_issue", risk="medium", scopes=["tracker:write"])
    async def tracker_create_issue(
        summary: str = "", assignee: str = "", priority: str = ""
    ) -> dict:
        "create"
        return {
            "key": "DARKHORSE-7",
            "summary": summary,
            "assignee": assignee or "Коля",
            "priority": priority or "normal",
            "deadline": "2026-06-14",
        }

    @platform_tool(name="tracker_comment_issue", risk="low", scopes=["tracker:write"])
    async def tracker_comment_issue(issue_key: str = "", text: str = "") -> dict:
        "comment"
        return {"issue_key": issue_key, "text": text, "comment_id": 1}

    @platform_tool(name="call_agent", risk="low", scopes=[])
    async def call_agent(target_agent: str = "", message: str = "") -> str:
        "delegate"
        return "**Статус**\n\n## Сделано\n- фича"


def _pm_agent():
    class _PM(BaseAgent):
        name = "pm_agent"
        description = "PM"
        prompt = "Ты исполнитель в Трекере."
        action_only = True
        tools = [
            "tracker_find_issues",
            "tracker_board_snapshot",
            "backlog_plan",
            "tracker_apply_backlog_plan",
            "tracker_create_issue",
            "tracker_comment_issue",
            "call_agent",
        ]
        llm_configs = [LLMSettings(model="yandexgpt", max_retries=0)]

    return _PM()


def _runner(agent):
    rc = RuntimeConfig(auto_risk=["low", "medium", "high"], confirm_risk=[])
    return ReActRunner(agent, runtime_config=rc, max_iterations=8)


# ---------------------------------------------------------------------------
# BOARD: backlog_plan -> apply forced edge, no second LLM call
# ---------------------------------------------------------------------------


class TestBoardStage:
    @patch.dict("os.environ", ENV)
    async def test_board_auto_chains_apply(self):
        _register_fake_tracker_tools()
        runner = _runner(_pm_agent())
        # LLM returns backlog_plan once; apply must be forced without a 2nd call.
        post = AsyncMock(return_value=_http_ok(_tool_call_response("backlog_plan", {"text": "t"})))
        with patch("httpx.AsyncClient.post", post):
            result = await runner.invoke("оформи доску из саммари " + "x" * 50, "b1")

        tool_names = [s.get("tool_name") for s in result.steps if s.get("kind") == "tool_result"]
        assert "backlog_plan" in tool_names
        assert "tracker_apply_backlog_plan" in tool_names
        assert "Доска:" in (result.reply or "")
        # Exactly one LLM call (the plan); apply was a forced edge.
        assert post.await_count == 1


# ---------------------------------------------------------------------------
# QUERY: read-only, blocks writes
# ---------------------------------------------------------------------------


class TestQueryStage:
    @patch.dict("os.environ", ENV)
    async def test_query_terminates_on_snapshot(self):
        _register_fake_tracker_tools()
        runner = _runner(_pm_agent())
        # QUERY flow: call 1 → tool call, call 2 → verbalization text
        post = AsyncMock(
            side_effect=[
                _http_ok(_tool_call_response("tracker_board_snapshot", {})),
                _http_ok(_text_response("На доске 5 задач.")),
            ]
        )
        with patch("httpx.AsyncClient.post", post):
            result = await runner.invoke("что на доске", "q1")
        kinds = [s.get("tool_name") for s in result.steps if s.get("kind") == "tool_result"]
        assert "tracker_board_snapshot" in kinds
        # QUERY needs 2 LLM calls: tool call + verbalization
        assert post.await_count == 2
        assert result.reply and "выполнено" not in result.reply

    @patch.dict("os.environ", ENV)
    async def test_query_blocks_write(self):
        _register_fake_tracker_tools()
        runner = _runner(_pm_agent())
        # LLM tries to create in a QUERY turn -> rejected, then gives up with text.
        post = AsyncMock(
            side_effect=[
                _http_ok(_tool_call_response("tracker_create_issue", {"summary": "x"})),
                _http_ok(_text_response("готово")),
            ]
        )
        with patch("httpx.AsyncClient.post", post):
            result = await runner.invoke("что на доске у Коли", "q2")
        errors = [s for s in result.steps if s.get("kind") == "tool_error"]
        assert any("QUERY" in (s.get("error") or "") for s in errors)
        results = [s for s in result.steps if s.get("kind") == "tool_result"]
        assert not any(s.get("tool_name") == "tracker_create_issue" for s in results)


# ---------------------------------------------------------------------------
# STATUS: terminal on comment; ordering enforced
# ---------------------------------------------------------------------------


class TestStatusStage:
    @patch.dict("os.environ", ENV)
    async def test_status_find_summarize_comment(self):
        _register_fake_tracker_tools()
        runner = _runner(_pm_agent())
        post = AsyncMock(
            side_effect=[
                _http_ok(_tool_call_response("tracker_find_issues", {"assignee": "Коля"})),
                _http_ok(
                    _tool_call_response(
                        "call_agent", {"target_agent": "meeting_summarizer", "message": "m"}
                    )
                ),
                _http_ok(
                    _tool_call_response(
                        "tracker_comment_issue", {"issue_key": "DARKHORSE-1", "text": "**Статус**"}
                    )
                ),
            ]
        )
        with patch("httpx.AsyncClient.post", post):
            result = await runner.invoke("Коля: добавил фичу, тесты проходят", "s1")
        kinds = [s.get("tool_name") for s in result.steps if s.get("kind") == "tool_result"]
        assert kinds == ["tracker_find_issues", "call_agent", "tracker_comment_issue"]
        assert "Комментарий" in (result.reply or "")


# ---------------------------------------------------------------------------
# INTAKE: create + assumptions reported; stage frozen (no flip-flop)
# ---------------------------------------------------------------------------


class TestIntakeStage:
    @patch.dict("os.environ", ENV)
    async def test_intake_creates_and_reports_assumptions(self):
        _register_fake_tracker_tools()
        runner = _runner(_pm_agent())
        post = AsyncMock(
            return_value=_http_ok(
                _tool_call_response("tracker_create_issue", {"summary": "MCP", "assignee": "Коля"})
            )
        )
        with patch("httpx.AsyncClient.post", post):
            result = await runner.invoke("создай Коле задачу MCP", "i1")
        assert "Создана DARKHORSE-7" in (result.reply or "")
        assert "Предположения:" in (result.reply or "")

    @patch.dict("os.environ", ENV)
    async def test_stage_frozen_not_rederived(self):
        """Stage is STATUS for «Имя: …» even if a later tool looks backlog-ish."""
        _register_fake_tracker_tools()
        runner = _runner(_pm_agent())
        # In a STATUS turn, the LLM (mis)fires backlog_plan -> must be rejected.
        post = AsyncMock(
            side_effect=[
                _http_ok(_tool_call_response("backlog_plan", {"text": "x"})),
                _http_ok(_text_response("ок")),
            ]
        )
        with patch("httpx.AsyncClient.post", post):
            result = await runner.invoke("Коля: длинный апдейт по задаче", "f1")
        errors = [s for s in result.steps if s.get("kind") == "tool_error"]
        assert any("backlog_plan" in (s.get("error") or "") for s in errors)


# ---------------------------------------------------------------------------
# DIALOG: prose reply, no tool steps
# ---------------------------------------------------------------------------


class TestDialogStage:
    @patch.dict("os.environ", ENV)
    async def test_dialog_returns_prose_without_tools(self):
        from core.goal import GoalPlan

        _register_fake_tracker_tools()
        runner = _runner(_pm_agent())

        async def _dialog_plan(message: str, *, use_llm: bool = True):
            return GoalPlan.dialog(message)

        dialog_reply = "Привет! Я PM-бот, помогаю с Трекером."
        post = AsyncMock(return_value=_http_ok(_text_response(dialog_reply)))
        with patch("core.react.build_goal_plan", side_effect=_dialog_plan):
            with patch("httpx.AsyncClient.post", post):
                result = await runner.invoke("привет, кто ты?", "dlg1")

        assert "PM" in (result.reply or "")
        assert post.await_count == 1
        tool_steps = [s for s in result.steps if s.get("kind") == "tool_call"]
        assert not tool_steps

    @patch.dict("os.environ", ENV)
    async def test_dialog_never_returns_empty_reply(self):
        from core.goal import GoalPlan

        _register_fake_tracker_tools()
        runner = _runner(_pm_agent())

        async def _dialog_plan(message: str, *, use_llm: bool = True):
            return GoalPlan.dialog(message)

        post = AsyncMock(return_value=_http_ok(_text_response("")))
        with patch("core.react.build_goal_plan", side_effect=_dialog_plan):
            with patch("httpx.AsyncClient.post", post):
                result = await runner.invoke("привет", "dlg-empty")

        assert result.reply
        assert "Я на связи" in result.reply


# ---------------------------------------------------------------------------
# Clarification on hard blocker (ambiguous find)
# ---------------------------------------------------------------------------


class TestClarification:
    @patch.dict("os.environ", ENV)
    async def test_status_ambiguous_find_returns_question(self):
        from core.goal import GoalPlan

        _register_fake_tracker_tools()
        runner = _runner(_pm_agent())

        async def _fake_execute(tool_name: str, tool_args: dict):
            if tool_name == "tracker_find_issues":
                return {
                    "count": 3,
                    "issues": [
                        {"key": "DARKHORSE-1"},
                        {"key": "DARKHORSE-2"},
                        {"key": "DARKHORSE-3"},
                    ],
                }
            tool = ToolRegistry().get(tool_name)
            validated = tool.validate_arguments(tool_args)
            result = tool.execute(**validated)
            if hasattr(result, "__await__"):
                return await result
            return result

        async def _status_plan(message: str, *, use_llm: bool = True):
            from core.stage_graph import StageId

            return GoalPlan.single(StageId.STATUS, message)

        post = AsyncMock(
            side_effect=[
                _http_ok(_tool_call_response("tracker_find_issues", {"assignee": "Коля"})),
                _http_ok(_text_response("уточните ключ")),
            ]
        )
        with patch("core.react.build_goal_plan", side_effect=_status_plan):
            with patch.object(runner, "_execute_tool", side_effect=_fake_execute):
                with patch("httpx.AsyncClient.post", post):
                    result = await runner.invoke("Коля: сделал релиз", "clar1")

        assert result.clarification is not None
        assert "Уточни" in (result.reply or "")
        assert any(s.get("kind") == "clarification" for s in result.steps)


# ---------------------------------------------------------------------------
# Multi-scenario turn plan
# ---------------------------------------------------------------------------


class TestMultiScenario:
    @patch.dict("os.environ", ENV)
    async def test_three_intents_run_three_scenarios(self):
        from core.stage_graph import StageId
        from core.goal import GoalItem, GoalPlan

        _register_fake_tracker_tools()
        runner = _runner(_pm_agent())

        async def _multi_plan(message: str, *, use_llm: bool = True):
            return GoalPlan(
                items=[
                    GoalItem(StageId.INTAKE, "создай Коле задачу MCP", intent="create"),
                    GoalItem(StageId.QUERY, "что на доске", intent="query"),
                    GoalItem(StageId.STATUS, "Коля: релиз готов", intent="status"),
                ]
            )

        post = AsyncMock(
            side_effect=[
                _http_ok(
                    _tool_call_response(
                        "tracker_create_issue", {"summary": "MCP", "assignee": "Коля"}
                    )
                ),
                _http_ok(_tool_call_response("tracker_board_snapshot", {})),
                # QUERY verbalization pass (data ready, LLM replies in words)
                _http_ok(_text_response("На доске 3 задачи.")),
                _http_ok(_tool_call_response("tracker_find_issues", {"assignee": "Коля"})),
                _http_ok(
                    _tool_call_response(
                        "call_agent", {"target_agent": "meeting_summarizer", "message": "m"}
                    )
                ),
                _http_ok(
                    _tool_call_response(
                        "tracker_comment_issue",
                        {"issue_key": "DARKHORSE-1", "text": "**Статус**"},
                    )
                ),
            ]
        )
        with patch("core.react.build_goal_plan", side_effect=_multi_plan):
            with patch("httpx.AsyncClient.post", post):
                result = await runner.invoke(
                    "создай Коле задачу MCP; что на доске; Коля: релиз готов",
                    "multi1",
                )

        assert "✓" in (result.reply or "")
        assert "INTAKE" in (result.reply or "")
        assert "QUERY" in (result.reply or "")
        assert "STATUS" in (result.reply or "")
