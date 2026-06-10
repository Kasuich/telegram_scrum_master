"""
Idempotent database seeding for single-tenant deployments.

The runtime persists actions/traces/confirms under a team (``Action.team_id``
is NOT NULL). For a single-tenant test/demo deployment we seed one default
Organization + Team with a well-known UUID supplied via ``DEFAULT_TEAM_ID``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update

from core.models import AgentInstance, AgentSpec, Organization, Team


async def ensure_default_team(
    session: Any,
    team_id: str,
    *,
    org_name: str = "Default Organization",
    team_name: str = "Default Team",
    tracker_queue: str = "TEST",
) -> Team:
    """Ensure a Team with ``team_id`` exists, creating org + team if needed.

    Idempotent: if the team already exists it is returned unchanged. Does not
    commit — the caller owns the transaction.

    Parameters
    ----------
    session:
        An ``AsyncSession``.
    team_id:
        UUID string for the default team (from ``DEFAULT_TEAM_ID``).
    """
    tid = uuid.UUID(team_id)

    existing = await session.get(Team, tid)
    if existing is not None:
        return existing

    org = Organization(id=uuid.uuid4(), name=org_name)
    session.add(org)
    await session.flush()

    team = Team(
        id=tid,
        organization_id=org.id,
        name=team_name,
        tracker_queue=tracker_queue,
    )
    session.add(team)
    await session.flush()
    return team


async def ensure_agent_instances(
    session: Any,
    team_id: str,
    agent_names: list[str],
) -> dict[str, AgentInstance]:
    """Idempotently create AgentInstance rows for each agent name.

    Returns a mapping ``agent_name → AgentInstance``. Used so that
    ``ScheduledJob.agent_instance_id`` (NOT NULL FK) can always be resolved.
    Does not commit — the caller owns the transaction.
    """
    tid = uuid.UUID(team_id)
    result: dict[str, AgentInstance] = {}

    for name in agent_names:
        stmt = select(AgentInstance).where(
            AgentInstance.team_id == tid,
            AgentInstance.name == name,
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = AgentInstance(
                id=uuid.uuid4(),
                team_id=tid,
                name=name,
                overlay={},
                enabled=True,
            )
            session.add(row)
            await session.flush()
        result[name] = row

    return result


async def ensure_default_agent_models(session: Any) -> None:
    """Apply code-owned model defaults without overwriting custom choices."""
    await session.execute(
        update(AgentSpec)
        .where(AgentSpec.name == "pm_agent", AgentSpec.model == "yandexgpt")
        .values(model="google/gemini-3.1-flash-lite")
    )
    await session.execute(
        update(AgentSpec)
        .where(AgentSpec.name == "pm_agent", AgentSpec.model == "gpt-oss-120b")
        .values(model="google/gemini-3.1-flash-lite")
    )


__all__ = [
    "ensure_default_team",
    "ensure_agent_instances",
    "ensure_default_agent_models",
]
