"""
Tests for call_agent @platform_tool.

All tests are unit-level: no real LLM, no real DB.
OrchestratorService is stubbed via a lightweight fake.
"""

from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("YC_API_KEY", "stub_key_00000000000000000000")
os.environ.setdefault("YC_FOLDER_ID", "b1g0000000000000000")
os.environ.setdefault("TRACKER_TOKEN", "stub_token_000000000000000000000")
os.environ.setdefault("TRACKER_ORG_ID", "000000000000")

from core.exceptions import ToolExecutionError
from core.react import AgentResult, PendingConfirm
from core.tools import ToolRegistry
from pm_orchestrator.tools.call_agent import (
    _call_chain,
    _sub_session_id,
    register_call_agent_tool,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_result(reply: str = "done", sid: str = "s1") -> AgentResult:
    return AgentResult(reply=reply, session_id=sid, steps=[])


def _confirm_result(sid: str = "s1") -> AgentResult:
    return AgentResult(
        pending_confirm=PendingConfirm(
            confirm_id="c1",
            tool_name="create_issue",
            tool_args={},
            risk="medium",
            prompt="Confirm?",
        ),
        session_id=sid,
        steps=[],
    )


class _FakeSvc:
    """Minimal OrchestratorService stub."""

    def __init__(self, agents: list[str], result: AgentResult | None = None) -> None:
        self._runners = {name: object() for name in agents}
        self._result = result or _text_result()
        self.calls: list[tuple[str, str, str]] = []

    async def invoke(self, agent_name: str, message: str, session_id: str) -> AgentResult:
        self.calls.append((agent_name, message, session_id))
        return self._result


@pytest.fixture(autouse=True)
def _clean():
    ToolRegistry().clear()
    _call_chain.set(())
    yield
    ToolRegistry().clear()
    _call_chain.set(())


# ---------------------------------------------------------------------------
# _sub_session_id
# ---------------------------------------------------------------------------


class TestSubSessionId:
    def test_deterministic(self) -> None:
        a = _sub_session_id(("pm_agent",), "meeting_summarizer")
        b = _sub_session_id(("pm_agent",), "meeting_summarizer")
        assert a == b

    def test_different_paths_differ(self) -> None:
        a = _sub_session_id((), "meeting_summarizer")
        b = _sub_session_id(("pm_agent",), "meeting_summarizer")
        assert a != b

    def test_different_targets_differ(self) -> None:
        a = _sub_session_id(("pm_agent",), "meeting_summarizer")
        b = _sub_session_id(("pm_agent",), "analytics_agent")
        assert a != b


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestCallAgentHappy:
    async def test_returns_sub_agent_reply(self) -> None:
        svc = _FakeSvc(["pm_agent", "meeting_summarizer"], _text_result("Action items: ..."))
        register_call_agent_tool(svc)

        tool = ToolRegistry().get("call_agent")
        result = await tool.execute(target_agent="meeting_summarizer", message="Summarise")

        assert result == "Action items: ..."
        assert len(svc.calls) == 1
        assert svc.calls[0][0] == "meeting_summarizer"
        assert svc.calls[0][1] == "Summarise"

    async def test_session_id_is_deterministic(self) -> None:
        svc = _FakeSvc(["pm_agent", "meeting_summarizer"])
        register_call_agent_tool(svc)

        tool = ToolRegistry().get("call_agent")
        await tool.execute(target_agent="meeting_summarizer", message="m1")
        await tool.execute(target_agent="meeting_summarizer", message="m2")

        # Both calls use the same stable sub-session (same delegation path)
        assert svc.calls[0][2] == svc.calls[1][2]

    async def test_call_chain_reset_after_call(self) -> None:
        """call_chain ContextVar must be cleaned up after the call."""
        svc = _FakeSvc(["pm_agent", "meeting_summarizer"])
        register_call_agent_tool(svc)

        assert _call_chain.get() == ()
        tool = ToolRegistry().get("call_agent")
        await tool.execute(target_agent="meeting_summarizer", message="go")
        assert _call_chain.get() == ()

    async def test_call_chain_reset_on_error(self) -> None:
        """ContextVar is cleaned up even when invoke raises."""
        svc = _FakeSvc(["pm_agent", "meeting_summarizer"])
        svc.invoke = lambda *_: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore

        async def _raise(*_: object, **__: object) -> AgentResult:
            raise RuntimeError("boom")

        svc.invoke = _raise  # type: ignore
        register_call_agent_tool(svc)

        tool = ToolRegistry().get("call_agent")
        with pytest.raises(RuntimeError):
            await tool.execute(target_agent="meeting_summarizer", message="go")

        assert _call_chain.get() == ()


# ---------------------------------------------------------------------------
# Guard: max depth
# ---------------------------------------------------------------------------


class TestCallAgentMaxDepth:
    async def test_raises_at_max_depth(self) -> None:
        svc = _FakeSvc(["a", "b", "c", "d"])
        register_call_agent_tool(svc)
        # Simulate being 3 levels deep already
        _call_chain.set(("a", "b", "c"))

        tool = ToolRegistry().get("call_agent")
        with pytest.raises(ToolExecutionError, match="max delegation depth"):
            await tool.execute(target_agent="d", message="go")

    async def test_allowed_at_depth_below_max(self) -> None:
        svc = _FakeSvc(["a", "b", "c"])
        register_call_agent_tool(svc)
        _call_chain.set(("a", "b"))  # depth 2, max is 3

        tool = ToolRegistry().get("call_agent")
        result = await tool.execute(target_agent="c", message="go")
        assert result == "done"


# ---------------------------------------------------------------------------
# Guard: cycle detection
# ---------------------------------------------------------------------------


class TestCallAgentCycleDetection:
    async def test_self_call_raises(self) -> None:
        svc = _FakeSvc(["pm_agent"])
        register_call_agent_tool(svc)
        _call_chain.set(("pm_agent",))

        tool = ToolRegistry().get("call_agent")
        with pytest.raises(ToolExecutionError, match="recursive cycle"):
            await tool.execute(target_agent="pm_agent", message="hi")

    async def test_indirect_cycle_raises(self) -> None:
        # a → b → a (cycle)
        svc = _FakeSvc(["a", "b"])
        register_call_agent_tool(svc)
        _call_chain.set(("a", "b"))

        tool = ToolRegistry().get("call_agent")
        with pytest.raises(ToolExecutionError, match="recursive cycle"):
            await tool.execute(target_agent="a", message="go")

    async def test_different_agents_allowed(self) -> None:
        svc = _FakeSvc(["a", "b", "c"])
        register_call_agent_tool(svc)
        _call_chain.set(("a",))

        tool = ToolRegistry().get("call_agent")
        result = await tool.execute(target_agent="b", message="go")
        assert result == "done"


# ---------------------------------------------------------------------------
# Guard: unknown agent
# ---------------------------------------------------------------------------


class TestCallAgentUnknownAgent:
    async def test_unknown_target_raises(self) -> None:
        svc = _FakeSvc(["pm_agent"])
        register_call_agent_tool(svc)

        tool = ToolRegistry().get("call_agent")
        with pytest.raises(ToolExecutionError, match="not found"):
            await tool.execute(target_agent="nonexistent", message="hi")

    async def test_error_lists_available_agents(self) -> None:
        svc = _FakeSvc(["pm_agent", "meeting_summarizer"])
        register_call_agent_tool(svc)

        tool = ToolRegistry().get("call_agent")
        with pytest.raises(ToolExecutionError, match="meeting_summarizer"):
            await tool.execute(target_agent="nope", message="hi")


# ---------------------------------------------------------------------------
# pending_confirm from sub-agent (MVP limitation)
# ---------------------------------------------------------------------------


class TestCallAgentPendingConfirm:
    async def test_pending_confirm_returns_message(self) -> None:
        svc = _FakeSvc(["pm_agent", "risky_agent"], _confirm_result())
        register_call_agent_tool(svc)

        tool = ToolRegistry().get("call_agent")
        result = await tool.execute(target_agent="risky_agent", message="do risky thing")

        assert isinstance(result, str)
        assert "подтверждени" in result.lower()


# ---------------------------------------------------------------------------
# Concurrent isolation
# ---------------------------------------------------------------------------


class TestCallAgentConcurrency:
    async def test_call_chains_are_isolated_across_concurrent_calls(self) -> None:
        """Two concurrent top-level invocations must not see each other's chain."""
        svc = _FakeSvc(["pm_agent", "meeting_summarizer", "analytics_agent"])
        register_call_agent_tool(svc)
        tool = ToolRegistry().get("call_agent")

        chains_seen: list[tuple[str, ...]] = []

        async def _wrapped_invoke(target: str) -> None:
            _call_chain.set(())
            chains_seen.append(_call_chain.get())
            await tool.execute(target_agent=target, message="go")
            chains_seen.append(_call_chain.get())

        await asyncio.gather(
            _wrapped_invoke("meeting_summarizer"),
            _wrapped_invoke("analytics_agent"),
        )

        # All observed chains must be empty (properly reset)
        assert all(c == () for c in chains_seen)
