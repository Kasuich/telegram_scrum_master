"""
Tests for ReActRunner (core.react).

All tests use in-memory session store (db_session=None) and mock LLM calls
so they run without a database or real YandexGPT credentials.

Scenarios covered:
  - Single-turn text reply (no tools)
  - Auto-execute low-risk tool → continue → final reply
  - Medium-risk tool → pending_confirm returned
  - Resume with approved=True → tool executed → final reply
  - Resume with approved=False → tool rejected → final reply
  - Unknown tool → error fed back → LLM gives text reply
  - always_confirm_tools overrides risk
  - Max iterations reached → graceful reply
  - Multiple auto-execute iterations (tool chain)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from core.agent import BaseAgent, LLMSettings
from core.config import RuntimeConfig
from core.exceptions import AgentError
from core.invocation import (
    InvocationContext,
    get_current_invocation_context,
)
from core.react import AgentResult, PendingConfirm, ReActRunner
from core.tools import ToolRegistry, platform_tool

# ---------------------------------------------------------------------------
# Env stub (needed by get_config() inside LLMClient)
# ---------------------------------------------------------------------------

ENV = {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test_db",
    "YC_API_KEY": "test_api_key_12345678901234567890",
    "YC_FOLDER_ID": "b1g1234567890abcdef",
    "TRACKER_TOKEN": "test_tracker_token_12345678901234567890",
    "TRACKER_ORG_ID": "12345678901234567890",
}

# ---------------------------------------------------------------------------
# Mock YandexGPT responses (foundationModels v1 format)
# ---------------------------------------------------------------------------


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


def _http_error(status: int) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = {}
    resp.text = "server error"

    def _raise() -> None:
        raise httpx.HTTPStatusError("err", request=MagicMock(), response=resp)

    resp.raise_for_status = _raise
    return resp


# ---------------------------------------------------------------------------
# Agent and tool fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_tool_registry():
    ToolRegistry().clear()
    yield
    ToolRegistry().clear()


@pytest.fixture
def low_tool():
    @platform_tool(name="search_issues", risk="low", scopes=["tracker:read"])
    async def search_issues(query: str) -> dict:
        "Search Tracker issues."
        return {"count": 3, "query": query}

    return search_issues


@pytest.fixture
def medium_tool():
    @platform_tool(name="create_issue", risk="medium", scopes=["tracker:write"])
    async def create_issue(queue: str, summary: str) -> dict:
        "Create a Tracker issue."
        return {"key": f"{queue}-42", "summary": summary}

    return create_issue


@pytest.fixture
def agent_with_tools(low_tool, medium_tool):
    class _PM(BaseAgent):
        name = "pm_agent"
        description = "PM assistant"
        prompt = "You are a PM agent."
        tools = ["search_issues", "create_issue"]
        llm_configs = [LLMSettings(model="yandexgpt", max_retries=0)]

    return _PM()


@pytest.fixture
def agent_no_tools():
    class _Chat(BaseAgent):
        name = "chat_agent"
        description = "Chatter"
        prompt = "You are a helpful assistant."
        llm_configs = [LLMSettings(model="yandexgpt", max_retries=0)]

    return _Chat()


@pytest.fixture
def agent_with_context_tool(context_tool):
    class _CtxAgent(BaseAgent):
        name = "ctx_agent"
        description = "Context reader"
        prompt = "You are a helpful assistant."
        tools = ["read_context"]
        llm_configs = [LLMSettings(model="yandexgpt", max_retries=0)]

    return _CtxAgent()


def _runner(agent, *, auto_risk=None, confirm_risk=None, always_confirm=None, max_iterations=8):
    rc = RuntimeConfig(
        auto_risk=auto_risk or ["low"],
        confirm_risk=confirm_risk or ["medium", "high"],
        always_confirm_tools=always_confirm or [],
    )
    return ReActRunner(agent, runtime_config=rc, max_iterations=max_iterations)


@pytest.fixture
def context_tool():
    @platform_tool(name="read_context", risk="low", scopes=["debug:read"])
    async def read_context() -> dict[str, Any]:
        "Read current invocation context."
        ctx = get_current_invocation_context()
        return ctx.model_dump(exclude_none=True) if ctx is not None else {}

    return read_context


# ---------------------------------------------------------------------------
# Tests: text-only reply
# ---------------------------------------------------------------------------


class TestTextReply:
    @patch.dict("os.environ", ENV)
    async def test_simple_text_reply(self, agent_no_tools):
        runner = _runner(agent_no_tools)
        mock_post = AsyncMock(return_value=_http_ok(_text_response("Hello there!")))
        with patch("httpx.AsyncClient.post", mock_post):
            result = await runner.invoke("Hi", "s1")

        assert isinstance(result, AgentResult)
        assert result.reply == "Hello there!"
        assert result.pending_confirm is None
        assert result.session_id == "s1"
        assert any(s["kind"] == "final" for s in result.steps)

    @patch.dict("os.environ", ENV)
    async def test_history_is_preserved_across_turns(self, agent_no_tools):
        runner = _runner(agent_no_tools)
        mock_post = AsyncMock(
            side_effect=[
                _http_ok(_text_response("First reply")),
                _http_ok(_text_response("Second reply")),
            ]
        )
        with patch("httpx.AsyncClient.post", mock_post):
            await runner.invoke("Turn 1", "s2")
            result = await runner.invoke("Turn 2", "s2")

        assert result.reply == "Second reply"
        # Second LLM call should have had the first exchange in history
        second_call_input = mock_post.call_args_list[1][1]["json"]["input"]
        roles = [m["role"] for m in second_call_input]
        assert "user" in roles
        assert "assistant" in roles

    @patch.dict("os.environ", ENV)
    async def test_history_is_compacted_into_summary_after_ten_messages(self, agent_no_tools):
        runner = _runner(agent_no_tools)
        calls_made: list[list[dict[str, Any]]] = []

        async def _post_spy(*args, **kwargs):
            calls_made.append(kwargs.get("json", {}).get("input", []))
            reply_no = len(calls_made)
            return _http_ok(_text_response(f"Reply {reply_no}"))

        with (
            patch("httpx.AsyncClient.post", _post_spy),
            patch.object(
                runner,
                "_summarize_session_context",
                AsyncMock(return_value="Team context summary."),
            ) as summarize,
        ):
            for idx in range(6):
                await runner.invoke(f"Turn {idx + 1}", "s_compact")

        assert summarize.await_count >= 1
        session = runner._mem_sessions["s_compact"]
        assert len(session["messages"]) == 10
        assert session["summary_context"] == "Team context summary."
        llm_messages = runner._llm_messages(
            runner._make_ctx(None, None),
            session["messages"],
            session_summary=session["summary_context"],
        )
        system_texts = [m.content for m in llm_messages if m.role == "system"]
        assert any("Team context summary." in text for text in system_texts)

    @patch.dict("os.environ", ENV)
    async def test_summary_context_is_injected_into_followup_llm_call(self, agent_no_tools):
        runner = _runner(agent_no_tools)
        payloads: list[dict[str, Any]] = []

        async def _post_spy(*args, **kwargs):
            payload = kwargs.get("json", {})
            payloads.append(payload)
            reply_no = len(payloads)
            return _http_ok(_text_response(f"Reply {reply_no}"))

        with (
            patch("httpx.AsyncClient.post", _post_spy),
            patch.object(
                runner,
                "_summarize_session_context",
                AsyncMock(return_value="Compressed team memory."),
            ),
        ):
            for idx in range(6):
                await runner.invoke(f"Turn {idx + 1}", "s_compact_followup")
            await runner.invoke("Turn 7", "s_compact_followup")

        assert any(
            "Compressed team memory." in str(payload.get("instructions", ""))
            for payload in payloads
        )

    @patch.dict("os.environ", ENV)
    async def test_compaction_falls_back_without_breaking_turn(self, agent_no_tools):
        runner = _runner(agent_no_tools)
        older_messages = [
            {"role": "user", "content": "Roman ведет backend и Telegram-интеграцию."},
            {"role": "assistant", "content": "Запомнил роли и текущий фокус команды."},
        ]

        with patch(
            "core.react.LLMClient.complete",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            summary = await runner._summarize_session_context(
                "Команда ведет PM-платформу.",
                older_messages,
            )

        assert "Команда ведет PM-платформу." in summary
        assert "Roman ведет backend и Telegram-интеграцию." in summary


# ---------------------------------------------------------------------------
# Tests: auto-execute (low risk)
# ---------------------------------------------------------------------------


class TestAutoExecute:
    @patch.dict("os.environ", ENV)
    async def test_auto_low_risk_tool(self, agent_with_tools):
        runner = _runner(agent_with_tools)
        # LLM: call tool → then text reply
        with patch(
            "httpx.AsyncClient.post",
            AsyncMock(
                side_effect=[
                    _http_ok(_tool_call_response("search_issues", {"query": "login bug"})),
                    _http_ok(_text_response("Found 3 issues for 'login bug'.")),
                ]
            ),
        ):
            result = await runner.invoke("Find login bugs", "s3")

        assert result.reply == "Found 3 issues for 'login bug'."
        assert result.pending_confirm is None
        kinds = [s["kind"] for s in result.steps]
        assert "tool_call" in kinds
        assert "tool_result" in kinds
        assert "final" in kinds

    @patch.dict("os.environ", ENV)
    async def test_tool_result_fed_back_to_llm(self, agent_with_tools):
        """After auto-execute, tool result appears in next LLM call messages."""
        runner = _runner(agent_with_tools)
        calls_made = []

        async def _post_spy(*args, **kwargs):
            calls_made.append(kwargs.get("json", {}).get("input", []))
            if len(calls_made) == 1:
                return _http_ok(_tool_call_response("search_issues", {"query": "q"}))
            return _http_ok(_text_response("Done"))

        with patch("httpx.AsyncClient.post", _post_spy):
            await runner.invoke("Find stuff", "s4")

        assert len(calls_made) == 2
        second_input = calls_made[1]
        texts = [m.get("content", "") for m in second_input]
        assert any("search_issues" in t for t in texts)

    @patch.dict("os.environ", ENV)
    async def test_tool_receives_invocation_context(self, agent_with_context_tool):
        runner = _runner(agent_with_context_tool)
        context = InvocationContext(
            channel="telegram",
            chat_id="-1001",
            message_id="42",
            actor_display_name="Ivan",
        )
        with patch(
            "httpx.AsyncClient.post",
            AsyncMock(
                side_effect=[
                    _http_ok(_tool_call_response("read_context", {})),
                    _http_ok(_text_response("Context received.")),
                ]
            ),
        ):
            result = await runner.invoke(
                "Read context",
                "telegram:s1",
                invocation_context=context,
            )

        tool_result = next(step for step in result.steps if step["kind"] == "tool_result")
        assert tool_result["result"]["channel"] == "telegram"
        assert tool_result["result"]["chat_id"] == "-1001"
        assert tool_result["result"]["message_id"] == "42"
        assert tool_result["result"]["actor_display_name"] == "Ivan"
        assert tool_result["result"]["agent_name"] == "ctx_agent"
        assert tool_result["result"]["session_id"] == "telegram:s1"

    @patch.dict("os.environ", ENV)
    async def test_llm_system_prompt_includes_transport_context(self, agent_no_tools):
        runner = _runner(agent_no_tools)
        context = InvocationContext(
            channel="telegram",
            actor_display_name="Roman Shinkarenko",
            metadata={"chat_type": "private"},
        )
        captured: list[dict[str, Any]] = []

        async def _post_spy(*args, **kwargs):
            captured.append(kwargs.get("json", {}))
            return _http_ok(_text_response("ok"))

        with patch("httpx.AsyncClient.post", _post_spy):
            await runner.invoke("создай задачу", "tg-1", invocation_context=context)

        instructions = captured[0].get("instructions", "")
        assert "Transport context:" in instructions
        assert "message_author: Roman Shinkarenko" in instructions


# ---------------------------------------------------------------------------
# Tests: confirm (medium risk)
# ---------------------------------------------------------------------------


class TestConfirmFlow:
    @patch.dict("os.environ", ENV)
    async def test_medium_risk_returns_pending_confirm(self, agent_with_tools):
        runner = _runner(agent_with_tools)
        with patch(
            "httpx.AsyncClient.post",
            AsyncMock(
                return_value=_http_ok(
                    _tool_call_response("create_issue", {"queue": "TEST", "summary": "Fix login"})
                )
            ),
        ):
            result = await runner.invoke("Create a task", "s5")

        assert result.reply is None
        assert isinstance(result.pending_confirm, PendingConfirm)
        assert result.pending_confirm.tool_name == "create_issue"
        assert result.pending_confirm.tool_args == {"queue": "TEST", "summary": "Fix login"}
        assert result.pending_confirm.risk == "medium"
        assert result.pending_confirm.confirm_id
        kinds = [s["kind"] for s in result.steps]
        assert "confirm_wait" in kinds

    @patch.dict("os.environ", ENV)
    async def test_resume_approved_executes_tool(self, agent_with_tools):
        runner = _runner(agent_with_tools)
        # First call → pending confirm
        with patch(
            "httpx.AsyncClient.post",
            AsyncMock(
                return_value=_http_ok(
                    _tool_call_response("create_issue", {"queue": "TEST", "summary": "Fix login"})
                )
            ),
        ):
            result = await runner.invoke("Create a task", "s6")

        confirm_id = result.pending_confirm.confirm_id

        # Resume → tool executes → LLM gets result → final reply
        with patch(
            "httpx.AsyncClient.post",
            AsyncMock(return_value=_http_ok(_text_response("Issue TEST-42 created successfully!"))),
        ):
            resumed = await runner.resume(confirm_id, approved=True)

        assert resumed.reply == "Issue TEST-42 created successfully!"
        assert resumed.pending_confirm is None
        kinds = [s["kind"] for s in resumed.steps]
        assert "tool_result" in kinds
        assert "final" in kinds
        # Tool actually ran and returned the expected dict
        tool_result_step = next(s for s in resumed.steps if s["kind"] == "tool_result")
        assert tool_result_step["result"]["key"] == "TEST-42"

    @patch.dict("os.environ", ENV)
    async def test_resume_rejected_skips_tool(self, agent_with_tools):
        runner = _runner(agent_with_tools)
        with patch(
            "httpx.AsyncClient.post",
            AsyncMock(
                return_value=_http_ok(
                    _tool_call_response("create_issue", {"queue": "TEST", "summary": "Fix login"})
                )
            ),
        ):
            result = await runner.invoke("Create a task", "s7")

        confirm_id = result.pending_confirm.confirm_id

        with patch(
            "httpx.AsyncClient.post",
            AsyncMock(
                return_value=_http_ok(_text_response("Understood, I won't create the issue."))
            ),
        ):
            resumed = await runner.resume(confirm_id, approved=False)

        assert resumed.reply == "Understood, I won't create the issue."
        kinds = [s["kind"] for s in resumed.steps]
        assert "confirm_rejected" in kinds
        assert "tool_result" not in kinds

    @patch.dict("os.environ", ENV)
    async def test_resume_unknown_confirm_raises(self, agent_no_tools):
        runner = _runner(agent_no_tools)
        with pytest.raises(AgentError, match="Confirm not found"):
            await runner.resume("nonexistent-id", approved=True)

    @patch.dict("os.environ", ENV)
    async def test_confirm_removed_after_resume(self, agent_with_tools):
        runner = _runner(agent_with_tools)
        with patch(
            "httpx.AsyncClient.post",
            AsyncMock(
                return_value=_http_ok(
                    _tool_call_response("create_issue", {"queue": "TEST", "summary": "x"})
                )
            ),
        ):
            result = await runner.invoke("Create", "s8")

        confirm_id = result.pending_confirm.confirm_id
        with patch(
            "httpx.AsyncClient.post", AsyncMock(return_value=_http_ok(_text_response("Done")))
        ):
            await runner.resume(confirm_id, approved=True)

        # Confirm should be gone — second resume raises
        with pytest.raises(AgentError, match="Confirm not found"):
            await runner.resume(confirm_id, approved=True)


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @patch.dict("os.environ", ENV)
    async def test_unknown_tool_feeds_error_back(self, agent_with_tools):
        runner = _runner(agent_with_tools)
        with patch(
            "httpx.AsyncClient.post",
            AsyncMock(
                side_effect=[
                    _http_ok(_tool_call_response("nonexistent_tool", {})),
                    _http_ok(_text_response("I cannot use that tool.")),
                ]
            ),
        ):
            result = await runner.invoke("Do something", "s9")

        assert result.reply == "I cannot use that tool."
        kinds = [s["kind"] for s in result.steps]
        assert "tool_error" in kinds

    @patch.dict("os.environ", ENV)
    async def test_max_iterations_reached(self, agent_with_tools):
        runner = _runner(agent_with_tools, max_iterations=2)
        # Always return a tool call → forces max iterations
        with patch(
            "httpx.AsyncClient.post",
            AsyncMock(return_value=_http_ok(_tool_call_response("search_issues", {"query": "x"}))),
        ):
            result = await runner.invoke("Keep searching", "s10")

        assert result.reply is not None
        assert result.pending_confirm is None
        # Should mention max iterations in steps
        final_step = next((s for s in result.steps if s["kind"] == "final"), None)
        assert final_step is not None
        assert final_step.get("reason") == "max_iterations"

    @patch.dict("os.environ", ENV)
    async def test_always_confirm_tools_overrides_low_risk(self, agent_with_tools):
        # search_issues is low-risk but forced to confirm
        runner = _runner(agent_with_tools, always_confirm=["search_issues"])
        with patch(
            "httpx.AsyncClient.post",
            AsyncMock(return_value=_http_ok(_tool_call_response("search_issues", {"query": "q"}))),
        ):
            result = await runner.invoke("Search", "s11")

        assert result.pending_confirm is not None
        assert result.pending_confirm.tool_name == "search_issues"

    @patch.dict("os.environ", ENV)
    async def test_tool_chain_two_auto_tools(self):
        """Two sequential low-risk tool calls, then final reply."""
        ToolRegistry().clear()

        @platform_tool(name="tool_a", risk="low")
        async def tool_a(x: str) -> str:
            "Tool A."
            return f"A({x})"

        @platform_tool(name="tool_b", risk="low")
        async def tool_b(x: str) -> str:
            "Tool B."
            return f"B({x})"

        class _Agent(BaseAgent):
            name = "chain_agent"
            description = "x"
            prompt = "x"
            tools = ["tool_a", "tool_b"]
            llm_configs = [LLMSettings(model="yandexgpt", max_retries=0)]

        runner = _runner(_Agent())
        with patch(
            "httpx.AsyncClient.post",
            AsyncMock(
                side_effect=[
                    _http_ok(_tool_call_response("tool_a", {"x": "hello"})),
                    _http_ok(_tool_call_response("tool_b", {"x": "world"})),
                    _http_ok(_text_response("All done.")),
                ]
            ),
        ):
            result = await runner.invoke("Do both", "s12")

        assert result.reply == "All done."
        kinds = [s["kind"] for s in result.steps]
        assert kinds.count("tool_call") == 2
        assert kinds.count("tool_result") == 2
        assert kinds[-1] == "final"

    @patch.dict("os.environ", ENV)
    async def test_session_isolation(self, agent_no_tools):
        """Different session_ids have independent history."""
        runner = _runner(agent_no_tools)
        with patch(
            "httpx.AsyncClient.post",
            AsyncMock(
                side_effect=[
                    _http_ok(_text_response("Reply for A")),
                    _http_ok(_text_response("Reply for B")),
                ]
            ),
        ):
            result_a = await runner.invoke("Hi from A", "session_a")
            result_b = await runner.invoke("Hi from B", "session_b")

        assert result_a.session_id == "session_a"
        assert result_b.session_id == "session_b"
        assert result_a.reply == "Reply for A"
        assert result_b.reply == "Reply for B"


class TestReActSystemPrompt:
    @patch.dict("os.environ", ENV)
    async def test_llm_call_includes_agent_system_prompt(self):
        from core.llm import LLMResponse

        captured: list = []

        class PromptAgent(BaseAgent):
            name = "prompt_agent"
            description = "test"
            prompt = "SYSTEM_PROMPT_MARKER_XYZ"
            action_only = True

        agent = PromptAgent()

        async def fake_call(messages, tool_schemas):
            captured.append(messages)
            return LLMResponse(content="", model="yandexgpt", tool_calls=None), 1

        runner = ReActRunner(agent)
        with patch.object(agent, "_call_with_fallback", fake_call):
            await runner.invoke("создай задачу", "sess_prompt")

        assert captured
        assert captured[0][0].role == "system"
        assert "SYSTEM_PROMPT_MARKER_XYZ" in captured[0][0].content


class TestActionOnlyReport:
    def test_human_readable_close(self):
        from core.react import _build_action_report

        steps = [
            {
                "kind": "tool_result",
                "tool_name": "tracker_close_issue",
                "result": {
                    "issue": {"key": "DARKHORSE-8", "summary": "CI", "status": "Закрыт"},
                },
            }
        ]
        assert _build_action_report(steps) == "Закрыта DARKHORSE-8 «CI» — Закрыт"

    def test_not_found(self):
        from core.react import _build_action_report

        steps = [
            {
                "kind": "tool_result",
                "tool_name": "tracker_search_issues",
                "result": {"count": 0, "issues": []},
            }
        ]
        assert _build_action_report(steps) == "Задача не найдена"

    def test_duplicate_found_create_reports_not_created(self):
        from core.react import _build_action_report

        steps = [
            {
                "kind": "tool_result",
                "tool_name": "tracker_create_issue",
                "result": {
                    "key": "DARKHORSE-42",
                    "summary": "Интеграция Telegram",
                    "duplicate_found": True,
                },
            }
        ]
        assert (
            _build_action_report(steps)
            == "Найден дубликат задачи DARKHORSE-42 «Интеграция Telegram». "
            "Новая карточка не создавалась"
        )

    def test_comment_report_over_call_agent(self):
        from core.react import _build_action_report

        steps = [
            {
                "kind": "tool_result",
                "tool_name": "call_agent",
                "result": "**Статус**\n\n## Сделано\n- фича",
            },
            {
                "kind": "tool_result",
                "tool_name": "tracker_comment_issue",
                "result": {"issue_key": "TEST-1", "text": "**Статус**"},
            },
        ]
        report = _build_action_report(steps)
        assert report.startswith("Комментарий к TEST-1")
