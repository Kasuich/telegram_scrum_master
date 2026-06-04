"""
Tests for core.scheduler — compute_next_run + SchedulerDaemon.

All tests are unit-level: no real DB, no real asyncio sleep.
The SchedulerDaemon tests use a fake session and a minimal OrchestratorService
stub so the DB path is exercised without Postgres.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from core.scheduler import SchedulerDaemon, compute_next_run

# ---------------------------------------------------------------------------
# compute_next_run
# ---------------------------------------------------------------------------


class TestComputeNextRun:
    def test_every_minute(self) -> None:
        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        nxt = compute_next_run("* * * * *", after=base)
        assert nxt == datetime(2026, 1, 1, 12, 1, 0, tzinfo=timezone.utc)

    def test_daily_9am(self) -> None:
        base = datetime(2026, 1, 1, 8, 59, 0, tzinfo=timezone.utc)
        nxt = compute_next_run("0 9 * * *", after=base)
        assert nxt == datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)

    def test_weekly_monday(self) -> None:
        # 2026-01-05 is a Monday; base = 2026-01-01 (Thursday)
        base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        nxt = compute_next_run("0 0 * * 1", after=base)
        assert nxt.weekday() == 0  # Monday

    def test_result_is_utc(self) -> None:
        base = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        nxt = compute_next_run("*/5 * * * *", after=base)
        assert nxt.tzinfo is timezone.utc

    def test_invalid_expression_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid cron"):
            compute_next_run("not a cron")

    def test_defaults_to_now(self) -> None:
        # Without `after`, should not raise and return a future datetime.
        nxt = compute_next_run("* * * * *")
        assert nxt > datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# SchedulerDaemon helpers
# ---------------------------------------------------------------------------


def _make_job(
    *,
    cron_expr: str = "* * * * *",
    run_count: int = 0,
    max_runs: int | None = None,
    payload: dict[str, Any] | None = None,
    enabled: bool = True,
) -> MagicMock:
    job = MagicMock()
    job.id = uuid.uuid4()
    job.cron_expr = cron_expr
    job.run_count = run_count
    job.max_runs = max_runs
    job.payload = payload or {"agent": "pm_agent", "message": "tick"}
    job.enabled = enabled
    job.next_run = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return job


def _make_svc(db_enabled: bool = True) -> MagicMock:
    svc = MagicMock()
    svc._db_enabled = db_enabled
    svc.invoke = AsyncMock(return_value=MagicMock(reply="ok"))
    return svc


class _FakeSession:
    def __init__(self, jobs: list[Any]) -> None:
        self._jobs = jobs
        self.flushed = False

    async def execute(self, stmt: Any) -> Any:
        result = MagicMock()
        result.scalars.return_value.all.return_value = list(self._jobs)
        return result

    async def flush(self) -> None:
        self.flushed = True

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# SchedulerDaemon._fire
# ---------------------------------------------------------------------------


class TestSchedulerDaemonFire:
    async def test_invokes_agent_from_payload(self) -> None:
        svc = _make_svc()
        daemon = SchedulerDaemon(svc)
        job = _make_job(payload={"agent": "meeting_summarizer", "message": "summarise"})
        session = MagicMock()
        session.flush = AsyncMock()

        await daemon._fire(session, job)

        svc.invoke.assert_awaited_once()
        call_args = svc.invoke.call_args[0]
        assert call_args[0] == "meeting_summarizer"
        assert call_args[1] == "summarise"

    async def test_run_count_incremented(self) -> None:
        svc = _make_svc()
        daemon = SchedulerDaemon(svc)
        job = _make_job(run_count=2)
        session = MagicMock()
        session.flush = AsyncMock()

        await daemon._fire(session, job)

        assert job.run_count == 3

    async def test_next_run_updated(self) -> None:
        svc = _make_svc()
        daemon = SchedulerDaemon(svc)
        job = _make_job(cron_expr="0 9 * * *")
        session = MagicMock()
        session.flush = AsyncMock()

        await daemon._fire(session, job)

        # next_run should be a future UTC datetime
        assert job.next_run > datetime.now(timezone.utc)
        assert job.enabled is True

    async def test_max_runs_disables_job(self) -> None:
        svc = _make_svc()
        daemon = SchedulerDaemon(svc)
        # run_count will become 3 == max_runs → disabled
        job = _make_job(run_count=2, max_runs=3)
        session = MagicMock()
        session.flush = AsyncMock()

        await daemon._fire(session, job)

        assert job.run_count == 3
        assert job.enabled is False

    async def test_failed_invoke_still_updates_accounting(self) -> None:
        svc = _make_svc()
        svc.invoke = AsyncMock(side_effect=RuntimeError("boom"))
        daemon = SchedulerDaemon(svc)
        job = _make_job()
        session = MagicMock()
        session.flush = AsyncMock()

        # Should NOT raise — errors are caught per-job
        await daemon._fire(session, job)

        assert job.run_count == 1  # still incremented

    async def test_invalid_cron_disables_job(self) -> None:
        svc = _make_svc()
        daemon = SchedulerDaemon(svc)
        job = _make_job(cron_expr="not-a-cron")
        session = MagicMock()
        session.flush = AsyncMock()

        await daemon._fire(session, job)

        assert job.enabled is False

    async def test_session_is_flushed(self) -> None:
        svc = _make_svc()
        daemon = SchedulerDaemon(svc)
        job = _make_job()
        session = MagicMock()
        session.flush = AsyncMock()

        await daemon._fire(session, job)

        session.flush.assert_awaited_once()

    async def test_stable_session_id_per_job(self) -> None:
        """Same job always gets the same session_id (history accumulates)."""
        svc = _make_svc()
        daemon = SchedulerDaemon(svc)
        job = _make_job()
        session = MagicMock()
        session.flush = AsyncMock()

        await daemon._fire(session, job)
        await daemon._fire(session, job)

        calls = svc.invoke.call_args_list
        assert calls[0][0][2] == calls[1][0][2]  # session_id identical


# ---------------------------------------------------------------------------
# SchedulerDaemon._tick
# ---------------------------------------------------------------------------


class TestSchedulerDaemonTick:
    async def test_skips_when_db_disabled(self) -> None:
        svc = _make_svc(db_enabled=False)
        daemon = SchedulerDaemon(svc)
        # Should return immediately without DB access
        with patch("core.db.get_session") as mock_get_session:
            await daemon._tick()
        mock_get_session.assert_not_called()

    async def test_fires_due_jobs(self) -> None:
        svc = _make_svc()
        daemon = SchedulerDaemon(svc)
        jobs = [_make_job(), _make_job()]
        fake_session = _FakeSession(jobs)

        with patch("core.db.get_session", return_value=fake_session):
            await daemon._tick()

        assert svc.invoke.await_count == 2

    async def test_no_due_jobs_no_invocations(self) -> None:
        svc = _make_svc()
        daemon = SchedulerDaemon(svc)
        fake_session = _FakeSession([])

        with patch("core.db.get_session", return_value=fake_session):
            await daemon._tick()

        svc.invoke.assert_not_awaited()


# ---------------------------------------------------------------------------
# SchedulerDaemon.run (loop behaviour)
# ---------------------------------------------------------------------------


class TestSchedulerDaemonRun:
    async def test_cancellation_stops_loop(self) -> None:
        svc = _make_svc(db_enabled=False)
        daemon = SchedulerDaemon(svc, tick_interval=0)

        task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)  # let one tick start
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_tick_error_does_not_kill_loop(self) -> None:
        """An unexpected tick error is swallowed; the loop keeps running."""
        svc = _make_svc(db_enabled=False)
        daemon = SchedulerDaemon(svc, tick_interval=0)

        call_count = 0

        async def bad_tick() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first tick fails")

        daemon._tick = bad_tick  # type: ignore[method-assign]

        task = asyncio.create_task(daemon.run())
        # Wait for at least two ticks
        for _ in range(5):
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        assert call_count >= 2
