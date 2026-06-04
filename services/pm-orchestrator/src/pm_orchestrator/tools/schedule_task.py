"""
schedule_task — @platform_tool that lets an agent create a ScheduledJob.

Guardrails:
- cron_expr validated via compute_next_run.
- Quota: max SCHEDULE_QUOTA enabled jobs per team (default 20).
- max_runs defaults to None (unlimited); pass an integer to cap executions.
- risk=medium → Autonomy Gate asks for confirmation before scheduling.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from core.exceptions import ToolExecutionError
from core.scheduler import compute_next_run
from core.tools import platform_tool

logger = logging.getLogger(__name__)

SCHEDULE_QUOTA = 20  # max enabled jobs per team


def register_schedule_task_tool(svc: Any) -> None:
    """Register schedule_task bound to *svc*.

    Must be called once at startup after ensure_schema_and_seed() so that
    AgentInstance rows already exist in the DB.
    """

    @platform_tool(name="schedule_task", risk="medium", scopes=["agent:schedule"])
    async def schedule_task(
        cron_expr: str,
        message: str,
        agent_name: str = "pm_agent",
        job_name: str = "",
        max_runs: int = 0,
    ) -> str:
        """Schedule a recurring task for an agent.

        Args:
            cron_expr: Standard 5-field cron expression (e.g. '0 9 * * 1').
            message: Message the agent will receive on each scheduled run.
            agent_name: Target agent name (default: pm_agent).
            job_name: Human-readable label for the job.
            max_runs: Maximum executions; 0 means unlimited.

        Returns:
            Confirmation string with job ID and first scheduled time.
        """
        if not svc._db_enabled or svc._team_id is None:
            raise ToolExecutionError(
                "schedule_task: DB persistence is disabled — "
                "set DATABASE_URL and DEFAULT_TEAM_ID to use scheduling."
            )

        # --- Validate cron ---
        try:
            first_run = compute_next_run(cron_expr)
        except ValueError as exc:
            raise ToolExecutionError(f"schedule_task: {exc}") from exc

        # --- Validate agent exists ---
        if agent_name not in svc._runners:
            available = ", ".join(svc._runners) or "(none)"
            raise ToolExecutionError(
                f"schedule_task: agent {agent_name!r} not found. Available: {available}"
            )

        import core.db as _db
        from core.models import AgentInstance, ScheduledJob
        from sqlalchemy import func, select

        get_session = _db.get_session
        team_uuid = uuid.UUID(svc._team_id)

        async with get_session() as session:
            # --- Quota check ---
            active_count: int = (
                await session.scalar(
                    select(func.count())
                    .select_from(ScheduledJob)
                    .join(AgentInstance, ScheduledJob.agent_instance_id == AgentInstance.id)
                    .where(
                        AgentInstance.team_id == team_uuid,
                        ScheduledJob.enabled.is_(True),
                    )
                )
                or 0
            )
            if active_count >= SCHEDULE_QUOTA:
                raise ToolExecutionError(
                    f"schedule_task: quota exceeded — team already has "
                    f"{active_count} active scheduled jobs (max {SCHEDULE_QUOTA})."
                )

            # --- Resolve AgentInstance ---
            instance = (
                await session.execute(
                    select(AgentInstance).where(
                        AgentInstance.team_id == team_uuid,
                        AgentInstance.name == agent_name,
                    )
                )
            ).scalar_one_or_none()

            if instance is None:
                # Lazily create if not seeded yet (e.g. newly added agent).
                from core.seed import ensure_agent_instances

                instances = await ensure_agent_instances(session, svc._team_id, [agent_name])
                instance = instances[agent_name]

            # --- Create job ---
            job = ScheduledJob(
                id=uuid.uuid4(),
                agent_instance_id=instance.id,
                name=job_name or f"{agent_name}:{cron_expr}",
                cron_expr=cron_expr,
                payload={"agent": agent_name, "message": message},
                max_runs=max_runs if max_runs > 0 else None,
                run_count=0,
                next_run=first_run,
                enabled=True,
            )
            session.add(job)
            await session.flush()
            job_id = str(job.id)

        max_info = f", max {max_runs} runs" if max_runs > 0 else ""
        return (
            f"Задача запланирована (id={job_id}). "
            f"Расписание: «{cron_expr}»{max_info}. "
            f"Первый запуск: {first_run.strftime('%Y-%m-%d %H:%M UTC')}."
        )


__all__ = ["register_schedule_task_tool", "SCHEDULE_QUOTA"]
