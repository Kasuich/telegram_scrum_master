"""
Tests for platform-api HTTP endpoints.

Uses FastAPI TestClient with a mocked ReActRunner so no real
LLM or Tracker calls are made.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest
from core.react import AgentResult, PendingConfirm
from fastapi.testclient import TestClient

# Minimal env so core.config doesn't fail
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("YC_API_KEY", "stub_key_00000000000000000000")
os.environ.setdefault("YC_FOLDER_ID", "b1g0000000000000000")
os.environ.setdefault("TRACKER_TOKEN", "stub_token_000000000000000000000")
os.environ.setdefault("TRACKER_ORG_ID", "000000000000")
os.environ.setdefault("TRACKER_ORG_TYPE", "cloud")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_runner():
    """Return a mock ReActRunner used by the app."""
    runner = AsyncMock()
    runner._mem_sessions = {}
    runner._mem_confirms = {}
    return runner


@pytest.fixture
def client(mock_runner):
    """TestClient with mocked runner injected via app state."""
    from platform_api.main import _state, app

    _state.runner = mock_runner
    _state.actions = []
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _text_result(reply: str = "Done", session_id: str = "s1") -> AgentResult:
    return AgentResult(
        reply=reply, session_id=session_id, steps=[{"kind": "final", "content": reply}]
    )


def _confirm_result(
    confirm_id: str = "c1",
    tool_name: str = "tracker_create_issue",
    session_id: str = "s1",
) -> AgentResult:
    return AgentResult(
        pending_confirm=PendingConfirm(
            confirm_id=confirm_id,
            tool_name=tool_name,
            tool_args={"queue": "TEST", "summary": "Fix bug"},
            risk="medium",
            prompt="Create issue?",
        ),
        session_id=session_id,
        steps=[{"kind": "confirm_wait", "confirm_id": confirm_id, "tool_name": tool_name}],
    )


def _action_result(session_id: str = "s1") -> AgentResult:
    return AgentResult(
        reply="Issue created.",
        session_id=session_id,
        steps=[
            {"kind": "tool_call", "tool_name": "tracker_create_issue", "tool_args": {}},
            {"kind": "tool_result", "tool_name": "tracker_create_issue", "result": {"key": "T-1"}},
            {"kind": "final", "content": "Issue created."},
        ],
    )


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------


class TestChat:
    def test_text_reply(self, client, mock_runner):
        mock_runner.invoke = AsyncMock(return_value=_text_result("Всё готово!"))
        r = client.post("/chat", json={"message": "Привет", "session_id": "s1"})
        assert r.status_code == 200
        data = r.json()
        assert data["reply"] == "Всё готово!"
        assert data["pending_confirm"] is None
        assert data["session_id"] == "s1"

    def test_pending_confirm(self, client, mock_runner):
        mock_runner.invoke = AsyncMock(return_value=_confirm_result())
        r = client.post("/chat", json={"message": "Создай задачу", "session_id": "s1"})
        assert r.status_code == 200
        data = r.json()
        assert data["reply"] is None
        pc = data["pending_confirm"]
        assert pc is not None
        assert pc["confirm_id"] == "c1"
        assert pc["tool_name"] == "tracker_create_issue"
        assert pc["risk"] == "medium"

    def test_auto_generates_session_id_if_missing(self, client, mock_runner):
        mock_runner.invoke = AsyncMock(return_value=_text_result(session_id="generated"))
        r = client.post("/chat", json={"message": "Hi"})
        assert r.status_code == 200
        # session_id was passed to invoke — check it's non-empty
        call_kwargs = mock_runner.invoke.call_args
        session_id_arg = call_kwargs[0][1]
        assert session_id_arg  # non-empty

    def test_empty_message_rejected(self, client, mock_runner):
        r = client.post("/chat", json={"message": "", "session_id": "s1"})
        assert r.status_code == 422

    def test_runner_exception_returns_500(self, client, mock_runner):
        mock_runner.invoke = AsyncMock(side_effect=RuntimeError("LLM down"))
        r = client.post("/chat", json={"message": "Hi", "session_id": "s1"})
        assert r.status_code == 500
        assert "LLM down" in r.json()["detail"]

    def test_steps_included_in_response(self, client, mock_runner):
        result = _text_result()
        result.steps = [{"kind": "final", "content": "Done", "ts": "2026-01-01"}]
        mock_runner.invoke = AsyncMock(return_value=result)
        r = client.post("/chat", json={"message": "Hi", "session_id": "s1"})
        assert r.json()["steps"][0]["kind"] == "final"


# ---------------------------------------------------------------------------
# POST /confirm/{id}
# ---------------------------------------------------------------------------


class TestConfirm:
    def test_approved_returns_reply(self, client, mock_runner):
        mock_runner.resume = AsyncMock(return_value=_text_result("Задача создана!"))
        r = client.post("/confirm/c1", json={"approved": True})
        assert r.status_code == 200
        assert r.json()["reply"] == "Задача создана!"
        mock_runner.resume.assert_called_once_with("c1", True)

    def test_rejected_returns_reply(self, client, mock_runner):
        mock_runner.resume = AsyncMock(return_value=_text_result("Понял, не создаю."))
        r = client.post("/confirm/c1", json={"approved": False})
        assert r.status_code == 200
        mock_runner.resume.assert_called_once_with("c1", False)

    def test_unknown_confirm_returns_404(self, client, mock_runner):
        from core.exceptions import AgentError

        mock_runner.resume = AsyncMock(side_effect=AgentError("Confirm not found: 'x'"))
        r = client.post("/confirm/x", json={"approved": True})
        assert r.status_code == 404

    def test_runner_exception_returns_500(self, client, mock_runner):
        mock_runner.resume = AsyncMock(side_effect=RuntimeError("Boom"))
        r = client.post("/confirm/c1", json={"approved": True})
        assert r.status_code == 500


# ---------------------------------------------------------------------------
# GET /actions
# ---------------------------------------------------------------------------


class TestActions:
    def _populate(self, client, mock_runner):
        """Run a chat that produces action steps."""
        mock_runner.invoke = AsyncMock(return_value=_action_result("sess"))
        client.post("/chat", json={"message": "Create", "session_id": "sess"})

    def test_returns_list(self, client, mock_runner):
        self._populate(client, mock_runner)
        r = client.get("/actions")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_logs_tool_call_steps(self, client, mock_runner):
        self._populate(client, mock_runner)
        r = client.get("/actions")
        kinds = [a["kind"] for a in r.json()]
        assert "tool_call" in kinds
        assert "tool_result" in kinds

    def test_filter_by_session_id(self, client, mock_runner):
        mock_runner.invoke = AsyncMock(side_effect=[_action_result("A"), _action_result("B")])
        client.post("/chat", json={"message": "m", "session_id": "A"})
        client.post("/chat", json={"message": "m", "session_id": "B"})
        r = client.get("/actions?session_id=A")
        actions = r.json()
        assert all(a["session_id"] == "A" for a in actions)

    def test_limit_param(self, client, mock_runner):
        mock_runner.invoke = AsyncMock(return_value=_action_result())
        for _ in range(5):
            client.post("/chat", json={"message": "m", "session_id": "s"})
        r = client.get("/actions?limit=2")
        assert len(r.json()) <= 2

    def test_empty_initially(self, client, mock_runner):
        r = client.get("/actions")
        assert r.json() == []


# ---------------------------------------------------------------------------
# GET /metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_returns_prometheus_format(self, client, mock_runner):
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]
        # Prometheus output always contains HELP lines
        assert b"#" in r.content or len(r.content) > 0
