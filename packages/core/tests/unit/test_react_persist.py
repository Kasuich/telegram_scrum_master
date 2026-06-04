"""
Tests for ReActRunner DB persistence wiring (B1).

Three layers:
  1. Pure helpers — `_session_uuid` mapping (no DB).
  2. `ensure_default_team` idempotency via a lightweight fake session.
  3. Full invoke→confirm→resume round trip against a REAL Postgres.
     Gated on the ``TEST_DATABASE_URL`` env var — skipped when absent so the
     default CI suite (no DB) stays green. To run locally::

         TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/test \
             uv run --extra test pytest tests/integration/test_react_persist.py
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from core.agent import BaseAgent, LLMSettings
from core.config import RuntimeConfig
from core.react import ReActRunner, _session_uuid
from core.seed import ensure_default_team
from core.tools import ToolRegistry, platform_tool

# ---------------------------------------------------------------------------
# Shared env + mock LLM helpers (mirrors test_react.py)
# ---------------------------------------------------------------------------

ENV = {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test_db",
    "YC_API_KEY": "test_api_key_12345678901234567890",
    "YC_FOLDER_ID": "b1g1234567890abcdef",
    "TRACKER_TOKEN": "test_tracker_token_12345678901234567890",
    "TRACKER_ORG_ID": "12345678901234567890",
}


def _text_response(text: str) -> dict[str, Any]:
    return {
        "result": {
            "alternatives": [{"message": {"role": "assistant", "text": text}, "status": "FINAL"}],
            "usage": {"inputTokens": "10", "completionTokens": "5", "totalTokens": "15"},
        }
    }


def _tool_call_response(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "result": {
            "alternatives": [
                {
                    "message": {
                        "role": "assistant",
                        "toolCallList": {
                            "toolCalls": [{"functionCall": {"name": name, "arguments": args}}]
                        },
                    },
                    "status": "TOOL_CALLS",
                }
            ],
            "usage": {"inputTokens": "20", "completionTokens": "10", "totalTokens": "30"},
        }
    }


def _http_ok(data: dict[str, Any]) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = data
    resp.text = json.dumps(data)
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# 1. _session_uuid — pure
# ---------------------------------------------------------------------------


class TestSessionUuid:
    def test_passthrough_for_valid_uuid(self) -> None:
        u = str(uuid.uuid4())
        assert str(_session_uuid(u)) == u

    def test_deterministic_for_arbitrary_string(self) -> None:
        a = _session_uuid("telegram:12345")
        b = _session_uuid("telegram:12345")
        assert isinstance(a, uuid.UUID)
        assert a == b

    def test_different_strings_differ(self) -> None:
        assert _session_uuid("s1") != _session_uuid("s2")


# ---------------------------------------------------------------------------
# 2. ensure_default_team — idempotent (fake session)
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal AsyncSession stand-in for seed idempotency tests."""

    def __init__(self, existing: Any | None = None) -> None:
        self._existing = existing
        self.added: list[Any] = []

    async def get(self, model: Any, pk: Any) -> Any:
        return self._existing

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass


class TestEnsureDefaultTeam:
    TEAM_ID = "00000000-0000-0000-0000-000000000001"

    async def test_creates_org_and_team_when_absent(self) -> None:
        session = _FakeSession(existing=None)
        team = await ensure_default_team(session, self.TEAM_ID)

        assert str(team.id) == self.TEAM_ID
        # Org + Team were added
        added_types = {type(o).__name__ for o in session.added}
        assert added_types == {"Organization", "Team"}

    async def test_idempotent_when_team_exists(self) -> None:
        sentinel = MagicMock()
        session = _FakeSession(existing=sentinel)
        team = await ensure_default_team(session, self.TEAM_ID)

        assert team is sentinel
        assert session.added == []  # nothing created


# ---------------------------------------------------------------------------
# 3. Full round trip against a real Postgres (gated)
# ---------------------------------------------------------------------------

TEST_DB_URL = os.getenv("TEST_DATABASE_URL")
pg_required = pytest.mark.skipif(
    not TEST_DB_URL, reason="TEST_DATABASE_URL not set — skipping real-DB round trip"
)

TEAM_ID = "00000000-0000-0000-0000-000000000099"


@pytest.fixture
def _clean_registry():
    ToolRegistry().clear()
    yield
    ToolRegistry().clear()


@pytest.fixture
async def db_factory():
    """Create a fresh schema in the test DB and yield a session factory."""
    from core.models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    # seed the team
    async with factory() as s:
        await ensure_default_team(s, TEAM_ID)
        await s.commit()

    yield factory

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def _make_agent():
    @platform_tool(name="create_issue", risk="medium", scopes=["tracker:write"])
    async def create_issue(queue: str, summary: str) -> dict:
        "Create a Tracker issue."
        return {"key": f"{queue}-42", "summary": summary}

    class _PM(BaseAgent):
        name = "pm_agent"
        description = "PM assistant"
        prompt = "You are a PM agent."
        tools = ["create_issue"]
        llm_configs = [LLMSettings(model="yandexgpt", max_retries=0)]

    return _PM()


@pg_required
class TestDbRoundTrip:
    @patch.dict("os.environ", ENV)
    async def test_confirm_flow_persists_rows(self, db_factory, _clean_registry) -> None:
        from core.models import Action, Confirm, Trace
        from sqlalchemy import func, select

        agent = _make_agent()
        rc = RuntimeConfig(auto_risk=["low"], confirm_risk=["medium", "high"])
        runner = ReActRunner(agent, runtime_config=rc)
        session_id = "telegram:42"  # non-UUID → exercises _session_uuid

        # --- invoke: medium-risk tool → pending confirm, rows persisted ---
        async with db_factory() as s:
            with patch(
                "httpx.AsyncClient.post",
                AsyncMock(
                    return_value=_http_ok(
                        _tool_call_response(
                            "create_issue", {"queue": "TEST", "summary": "Fix login"}
                        )
                    )
                ),
            ):
                result = await runner.invoke(
                    "Create a task", session_id, db_session=s, team_id=TEAM_ID
                )
            await s.commit()

        assert result.pending_confirm is not None
        confirm_id = result.pending_confirm.confirm_id

        # Verify rows exist
        async with db_factory() as s:
            trace_count = await s.scalar(
                select(func.count())
                .select_from(Trace)
                .where(Trace.session_id == _session_uuid(session_id))
            )
            action = (
                await s.execute(select(Action).where(Action.id == uuid.UUID(confirm_id)))
            ).scalar_one()
            confirm = (
                await s.execute(select(Confirm).where(Confirm.id == uuid.UUID(confirm_id)))
            ).scalar_one()

        assert trace_count == 1
        assert action.status == "pending"
        assert action.risk_level == "medium"
        assert str(action.team_id) == TEAM_ID
        assert action.input == {"queue": "TEST", "summary": "Fix login"}
        assert confirm.status == "pending"

        # --- resume approved: tool executes, statuses flip ---
        async with db_factory() as s:
            with patch(
                "httpx.AsyncClient.post",
                AsyncMock(return_value=_http_ok(_text_response("Issue TEST-42 created!"))),
            ):
                resumed = await runner.resume(
                    confirm_id, approved=True, db_session=s, team_id=TEAM_ID
                )
            await s.commit()

        assert resumed.reply == "Issue TEST-42 created!"

        async with db_factory() as s:
            action = (
                await s.execute(select(Action).where(Action.id == uuid.UUID(confirm_id)))
            ).scalar_one()
            confirm = (
                await s.execute(select(Confirm).where(Confirm.id == uuid.UUID(confirm_id)))
            ).scalar_one()

        assert action.status == "completed"
        assert confirm.status == "approved"
        assert confirm.responded_at is not None
