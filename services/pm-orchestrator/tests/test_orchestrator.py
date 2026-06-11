"""Tests for OrchestratorService and JSON-RPC endpoint."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from core.agent import BaseAgent, LLMSettings
from core.exceptions import AgentError
from core.invocation import InvocationContext
from core.react import AgentResult, PendingConfirm
from core.tools import ToolRegistry
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("YC_API_KEY", "stub_key_00000000000000000000")
os.environ.setdefault("YC_FOLDER_ID", "b1g0000000000000000")
os.environ.setdefault("OPENROUTER_API_KEY", "sk-or-v1-test-stub-key-0000000000")
os.environ.setdefault("TRACKER_TOKEN", "stub_token_000000000000000000000")
os.environ.setdefault("TRACKER_ORG_ID", "000000000000")
os.environ.setdefault("TRACKER_ORG_TYPE", "cloud")


@pytest.fixture(autouse=True)
def _clean_registry():
    ToolRegistry().clear()
    yield
    ToolRegistry().clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(name: str) -> BaseAgent:
    cls = type(
        name,
        (BaseAgent,),
        {
            "name": name,
            "description": f"Agent {name}",
            "prompt": "You are helpful.",
            "tools": [],
            "llm_configs": [LLMSettings(model="yandexgpt")],
        },
    )
    return cls()


def _text(reply: str = "Done", sid: str = "s1") -> AgentResult:
    return AgentResult(reply=reply, session_id=sid, steps=[])


def _confirm(cid: str = "c1", sid: str = "s1") -> AgentResult:
    return AgentResult(
        pending_confirm=PendingConfirm(
            confirm_id=cid,
            tool_name="create",
            tool_args={},
            risk="medium",
            prompt="Confirm?",
        ),
        session_id=sid,
        steps=[],
    )


# ---------------------------------------------------------------------------
# OrchestratorService
# ---------------------------------------------------------------------------


class TestOrchestratorService:
    def _svc(self, *agents):
        from pm_orchestrator.orchestrator import OrchestratorService

        svc = OrchestratorService()
        for a in agents:
            svc._register(a)
        return svc

    def test_list_agents(self):
        svc = self._svc(_make_agent("alpha"), _make_agent("beta"))
        names = [a["name"] for a in svc.list_agents()]
        assert "alpha" in names
        assert "beta" in names

    async def test_invoke(self):
        svc = self._svc(_make_agent("alpha"))
        with patch.object(svc._runners["alpha"], "invoke", AsyncMock(return_value=_text())):
            result = await svc.invoke("alpha", "hello", "s1")
        assert result.reply == "Done"

    async def test_invoke_passes_context(self):
        svc = self._svc(_make_agent("alpha"))
        ctx = InvocationContext(channel="telegram", chat_id="-1001", message_id="42")
        with patch.object(
            svc._runners["alpha"], "invoke", AsyncMock(return_value=_text())
        ) as mocked:
            await svc.invoke("alpha", "hello", "s1", context=ctx)
        assert mocked.call_args.kwargs["invocation_context"] == ctx

    async def test_invoke_short_circuits_telemost_link(self):
        svc = self._svc(_make_agent("pm_agent"))
        telemost_url = "https://telemost.yandex.ru/j/12345678901234567"
        with (
            patch.object(svc._runners["pm_agent"], "invoke", AsyncMock()) as runner_invoke,
            patch(
                "core.telemost_shortcut.schedule_meeting_capture",
                AsyncMock(return_value={"meeting_id": "m1", "status": "recording"}),
            ),
        ):
            result = await svc.invoke("pm_agent", telemost_url, "s1")

        runner_invoke.assert_not_awaited()
        assert result.reply is not None
        assert "m1" in result.reply
        assert result.steps[0]["kind"] == "meeting_capture_scheduled"

    async def test_invoke_unknown_agent_raises(self):
        svc = self._svc()
        with pytest.raises(KeyError, match="not found"):
            await svc.invoke("ghost", "hi", "s1")

    async def test_resume_routes_via_confirm_index(self):
        svc = self._svc(_make_agent("alpha"))
        # Manually prime the confirm index
        svc._confirm_index["c42"] = "alpha"
        with patch.object(
            svc._runners["alpha"], "resume", AsyncMock(return_value=_text("resumed"))
        ):
            result = await svc.resume("c42", approved=True)
        assert result.reply == "resumed"

    async def test_resume_unknown_confirm_raises(self):
        svc = self._svc(_make_agent("alpha"))
        with pytest.raises(KeyError, match="not found"):
            await svc.resume("no-such-id", approved=True)

    async def test_resume_uses_db_lookup_when_confirm_not_in_memory(self):
        svc = self._svc(_make_agent("alpha"))
        with (
            patch.object(
                svc,
                "_lookup_agent_name_for_confirm_db",
                AsyncMock(return_value="alpha"),
            ) as lookup,
            patch.object(
                svc._runners["alpha"],
                "resume",
                AsyncMock(return_value=_text("resumed")),
            ),
        ):
            result = await svc.resume("c42", approved=True)
        lookup.assert_awaited_once_with("c42")
        assert result.reply == "resumed"

    async def test_confirm_indexed_on_invoke_with_pending(self):
        svc = self._svc(_make_agent("alpha"))
        with patch.object(svc._runners["alpha"], "invoke", AsyncMock(return_value=_confirm("c99"))):
            await svc.invoke("alpha", "create", "s1")
        assert svc._confirm_index.get("c99") == "alpha"

    async def test_confirm_removed_after_resume(self):
        svc = self._svc(_make_agent("alpha"))
        svc._confirm_index["cX"] = "alpha"
        with patch.object(svc._runners["alpha"], "resume", AsyncMock(return_value=_text())):
            await svc.resume("cX", approved=False)
        assert "cX" not in svc._confirm_index

    async def test_actions_logged(self):
        svc = self._svc(_make_agent("alpha"))
        result = AgentResult(
            reply="ok",
            session_id="s1",
            steps=[
                {"kind": "tool_call", "tool_name": "x"},
                {"kind": "final", "content": "ok"},
            ],
        )
        with patch.object(svc._runners["alpha"], "invoke", AsyncMock(return_value=result)):
            await svc.invoke("alpha", "hi", "s1")
        assert any(a["kind"] == "tool_call" for a in svc.actions)

    async def test_actions_carry_gantt_label_and_state(self):
        svc = self._svc(_make_agent("alpha"))
        result = AgentResult(
            reply="ok",
            session_id="s1",
            steps=[
                {"kind": "stage", "stage": "BOARD", "ts": "2026-06-12T00:00:00+00:00"},
                {
                    "kind": "tool_call",
                    "tool_name": "tracker_create_issue",
                    "ts": "2026-06-12T00:00:01+00:00",
                },
                {
                    "kind": "tool_result",
                    "tool_name": "tracker_create_issue",
                    "result": {"error": "boom"},
                    "ts": "2026-06-12T00:00:03+00:00",
                },
                {"kind": "final", "content": "done", "ts": "2026-06-12T00:00:04+00:00"},
            ],
        )
        with patch.object(svc._runners["alpha"], "invoke", AsyncMock(return_value=result)):
            await svc.invoke("alpha", "create a task", "s1")

        by_kind = {a["kind"]: a for a in svc.actions}
        # Bar text (textField) and color bucket (colorByField) are present on every step.
        assert all("label" in a and "state" in a for a in svc.actions)
        assert by_kind["tool_call"]["label"] == "tracker_create_issue"
        assert by_kind["tool_call"]["state"] == "call"
        # A tool_result carrying an error must color as "error", not "ok".
        assert by_kind["tool_result"]["state"] == "error"
        assert by_kind["stage"]["state"] == "stage"
        # Gantt start/end timestamps wired for the timeline.
        assert by_kind["tool_call"]["end_ts"] == "2026-06-12T00:00:03+00:00"
        # Source message captured on the first step for trace search.
        assert by_kind["stage"]["trace_label"] == "create a task"

    async def test_invoke_blocked_when_agent_disabled(self):
        svc = self._svc(_make_agent("alpha"))
        svc._db_enabled = True
        svc._team_id = "00000000-0000-0000-0000-000000000001"

        class FakeSession:
            async def execute(self, stmt):
                del stmt
                return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(enabled=False))

        @asynccontextmanager
        async def fake_get_session():
            yield FakeSession()

        with patch("core.db.get_session", fake_get_session):
            with pytest.raises(AgentError, match="disabled"):
                await svc.invoke("alpha", "hi", "s1")

    async def test_startup_seeds_daily_digest_job(self):
        svc = self._svc(_make_agent("pm_agent"))
        svc._db_enabled = True
        svc._team_id = "00000000-0000-0000-0000-000000000001"

        class FakeSession:
            pass

        @asynccontextmanager
        async def fake_get_session():
            yield FakeSession()

        with (
            patch("core.db.create_all_tables", AsyncMock()),
            patch("core.db.get_session", fake_get_session),
            patch("core.seed.ensure_default_team", AsyncMock()),
            patch("core.seed.ensure_agent_instances", AsyncMock()),
            patch("core.seed.ensure_default_agent_models", AsyncMock()) as ensure_models,
            patch("core.daily_digest.ensure_daily_digest_scheduled_job", AsyncMock()) as ensure_job,
        ):
            await svc.ensure_schema_and_seed()

        ensure_models.assert_awaited_once()
        ensure_job.assert_awaited_once()


# ---------------------------------------------------------------------------
# JSON-RPC endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def rpc_client():
    from pm_orchestrator.rpc import _svc, app

    _svc._runners.clear()
    _svc._confirm_index.clear()
    _svc.actions.clear()
    _svc._register(_make_agent("pm_agent"))
    return TestClient(app)


class TestRpcEndpoint:
    def _call(self, client, method, **params):
        return client.post(
            "/rpc",
            json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        )

    def test_list_agents(self, rpc_client):
        r = self._call(rpc_client, "list_agents")
        assert r.status_code == 200
        result = r.json()["result"]
        assert any(a["name"] == "pm_agent" for a in result)

    def test_invoke(self, rpc_client):
        from pm_orchestrator.rpc import _svc

        with patch.object(
            _svc._runners["pm_agent"], "invoke", AsyncMock(return_value=_text("hello"))
        ):
            r = self._call(rpc_client, "invoke", agent="pm_agent", message="hi", session_id="s1")
        assert r.json()["result"]["reply"] == "hello"

    def test_resume_not_found(self, rpc_client):
        r = self._call(rpc_client, "resume", confirm_id="nope", approved=True)
        # JSON-RPC 2.0: always HTTP 200, error communicated in body
        assert r.status_code == 200
        assert "error" in r.json()

    def test_unknown_method(self, rpc_client):
        r = self._call(rpc_client, "nonexistent")
        assert r.json()["error"]["code"] == -32601

    def test_health(self, rpc_client):
        r = rpc_client.get("/health")
        assert r.status_code == 200
        assert "pm_agent" in r.json()["agents"]
