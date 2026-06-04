"""
Scheduler utilities for PM Agent Platform.

Provides:
- ``compute_next_run`` — next fire time for a cron expression.
- ``SchedulerDaemon`` — asyncio background loop that executes due
  ``ScheduledJob`` rows from the DB with ``SELECT … FOR UPDATE SKIP LOCKED``
  (safe for multiple orchestrator replicas).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from croniter import CroniterBadCronError, croniter

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Maximum jobs processed per tick (prevents one tick from hogging the loop).
_TICK_BATCH = 20
# Interval between ticks in seconds.
_TICK_INTERVAL = 60


def compute_next_run(cron_expr: str, after: datetime | None = None) -> datetime:
    """Return the next fire time for *cron_expr* after *after*.

    Parameters
    ----------
    cron_expr:
        Standard 5-field cron expression (``* * * * *``).
    after:
        Reference point; defaults to ``datetime.now(UTC)``.

    Raises
    ------
    ValueError
        If *cron_expr* is not a valid cron expression.
    """
    base = after or datetime.now(timezone.utc)
    # croniter works with naive datetimes; strip tz, reattach afterwards.
    base_naive = base.replace(tzinfo=None)
    try:
        cron = croniter(cron_expr, base_naive)
        next_naive: datetime = cron.get_next(datetime)
    except (CroniterBadCronError, ValueError) as exc:
        raise ValueError(f"Invalid cron expression {cron_expr!r}: {exc}") from exc
    return next_naive.replace(tzinfo=timezone.utc)


class SchedulerDaemon:
    """Background asyncio task that fires due scheduled jobs.

    Usage::

        daemon = SchedulerDaemon(orchestrator_service)
        task = asyncio.create_task(daemon.run())
        # on shutdown:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    """

    def __init__(self, svc: Any, *, tick_interval: int = _TICK_INTERVAL) -> None:
        self._svc = svc
        self._tick_interval = tick_interval

    async def run(self) -> None:
        """Main loop — ticks every ``tick_interval`` seconds."""
        logger.info("SchedulerDaemon started (interval=%ds)", self._tick_interval)
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                logger.info("SchedulerDaemon cancelled")
                raise
            except Exception:
                logger.exception("SchedulerDaemon unexpected error in tick")
            try:
                await asyncio.sleep(self._tick_interval)
            except asyncio.CancelledError:
                logger.info("SchedulerDaemon cancelled during sleep")
                raise

    async def _tick(self) -> None:
        """Process all due jobs in one batch."""
        if not self._svc._db_enabled:
            return

        from sqlalchemy import select

        import core.db as _db
        from core.models import ScheduledJob

        get_session = _db.get_session

        now = datetime.now(timezone.utc)

        async with get_session() as session:
            # SKIP LOCKED: concurrent replicas each grab distinct rows.
            stmt = (
                select(ScheduledJob)
                .where(
                    ScheduledJob.enabled.is_(True),
                    ScheduledJob.next_run <= now,
                )
                .order_by(ScheduledJob.next_run)
                .limit(_TICK_BATCH)
                .with_for_update(skip_locked=True)
            )
            rows = (await session.execute(stmt)).scalars().all()

            for job in rows:
                await self._fire(session, job)

    async def _fire(self, session: Any, job: Any) -> None:
        """Execute one job and update its state."""

        agent_name: str = job.payload.get("agent", "pm_agent")
        message: str = job.payload.get("message", "")
        # Derive a stable session_id from the job so history accumulates.
        session_id = str(uuid.uuid5(uuid.UUID("d1a2b3c4-e5f6-7890-abcd-ef1234567890"), str(job.id)))

        try:
            await self._svc.invoke(agent_name, message, session_id)
            logger.info("Scheduler fired job %s (agent=%s)", job.id, agent_name)
        except Exception:
            logger.exception("Scheduler: job %s failed", job.id)

        # Update accounting regardless of success/failure.
        job.run_count += 1
        if job.max_runs is not None and job.run_count >= job.max_runs:
            job.enabled = False
            logger.info("Scheduler: job %s reached max_runs=%d, disabled", job.id, job.max_runs)
        else:
            try:
                job.next_run = compute_next_run(job.cron_expr)
            except ValueError:
                logger.warning(
                    "Scheduler: job %s has invalid cron %r — disabling", job.id, job.cron_expr
                )
                job.enabled = False
        await session.flush()


__all__ = ["SchedulerDaemon", "compute_next_run"]
