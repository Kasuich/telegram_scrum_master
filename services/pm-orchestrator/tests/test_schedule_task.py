"""Tests for schedule_task @platform_tool (unit, no real DB)."""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("YC_API_KEY", "stub_key_00000000000000000000")
os.environ.setdefault("YC_FOLDER_ID", "b1g0000000000000000")
os.environ.setdefault("TRACKER_TOKEN", "stub_token_000000000000000000000")
os.environ.setdefault("TRACKER_ORG_ID", "000000000000")

from core.exceptions import ToolExecutionError
from core.models import ScheduledJob
from core.tools import ToolRegistry
from pm_orchestrator.tools.schedule_task import SCHEDULE_QUOTA, register_schedule_task_tool

_TEAM_ID = "00000000-0000-0000-0000-000000000001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(active_jobs: int = 0, instance_id: uuid.UUID | None = None) -> MagicMock:
    """Build a fake AsyncSession."""
    inst = MagicMock()
    inst.id = instance_id or uuid.uuid4()

    session = MagicMock()
    session.scalar = AsyncMock(return_value=active_jobs)
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=inst))
    )
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


def _make_svc(
    *,
    db_enabled: bool = True,
    agents: list[str] | None = None,
    active_jobs: int = 0,
) -> tuple[Any, MagicMock]:
    svc = MagicMock()
    svc._db_enabled = db_enabled
    svc._team_id = _TEAM_ID if db_enabled else None
    svc._runners = {name: object() for name in (agents or ["pm_agent"])}
    session = _make_session(active_jobs=active_jobs)
    return svc, session


@asynccontextmanager
async def _session_ctx(session: MagicMock):
    yield session


@pytest.fixture(autouse=True)
def _clean():
    ToolRegistry().clear()
    yield
    ToolRegistry().clear()


async def _invoke(svc: Any, session: MagicMock, **kwargs: Any) -> str:
    with patch("core.db.get_session", return_value=_session_ctx(session)):
        register_schedule_task_tool(svc)
        tool = ToolRegistry().get("schedule_task")
        return await tool.execute(**{"cron_expr": "* * * * *", "message": "m", **kwargs})


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestScheduleTaskHappy:
    async def test_returns_confirmation_string(self) -> None:
        svc, session = _make_svc()
        result = await _invoke(svc, session, cron_expr="0 9 * * 1", message="Стендап")
        assert "запланирована" in result
        assert "0 9 * * 1" in result
        assert "UTC" in result

    async def test_job_added_to_session(self) -> None:
        svc, session = _make_svc()
        await _invoke(svc, session)
        session.add.assert_called_once()
        added = session.add.call_args[0][0]
        assert isinstance(added, ScheduledJob)
        assert added.cron_expr == "* * * * *"
        assert added.enabled is True

    async def test_payload_contains_agent_and_message(self) -> None:
        svc, session = _make_svc(agents=["pm_agent"])
        await _invoke(svc, session, message="do the thing", agent_name="pm_agent")
        added = session.add.call_args[0][0]
        assert added.payload["agent"] == "pm_agent"
        assert added.payload["message"] == "do the thing"

    async def test_max_runs_set_when_nonzero(self) -> None:
        svc, session = _make_svc()
        await _invoke(svc, session, max_runs=5)
        added = session.add.call_args[0][0]
        assert added.max_runs == 5

    async def test_max_runs_none_when_zero(self) -> None:
        svc, session = _make_svc()
        await _invoke(svc, session, max_runs=0)
        added = session.add.call_args[0][0]
        assert added.max_runs is None

    async def test_first_run_in_future(self) -> None:
        from datetime import datetime, timezone

        svc, session = _make_svc()
        await _invoke(svc, session)
        added = session.add.call_args[0][0]
        assert added.next_run > datetime.now(timezone.utc)

    async def test_max_runs_shown_in_result(self) -> None:
        svc, session = _make_svc()
        result = await _invoke(svc, session, max_runs=3)
        assert "3" in result


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


class TestScheduleTaskGuardrails:
    async def test_invalid_cron_raises(self) -> None:
        svc, session = _make_svc()
        with pytest.raises(ToolExecutionError, match="Invalid cron"):
            await _invoke(svc, session, cron_expr="not-a-cron")

    async def test_db_disabled_raises(self) -> None:
        svc, session = _make_svc(db_enabled=False)
        with pytest.raises(ToolExecutionError, match="DB persistence is disabled"):
            await _invoke(svc, session)

    async def test_unknown_agent_raises(self) -> None:
        svc, session = _make_svc(agents=["pm_agent"])
        with pytest.raises(ToolExecutionError, match="not found"):
            await _invoke(svc, session, agent_name="nonexistent")

    async def test_quota_exceeded_raises(self) -> None:
        svc, session = _make_svc(active_jobs=SCHEDULE_QUOTA)
        with pytest.raises(ToolExecutionError, match="quota exceeded"):
            await _invoke(svc, session)

    async def test_quota_at_limit_minus_one_passes(self) -> None:
        svc, session = _make_svc(active_jobs=SCHEDULE_QUOTA - 1)
        result = await _invoke(svc, session)
        assert "запланирована" in result
