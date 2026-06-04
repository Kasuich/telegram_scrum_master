"""
Idempotent database seeding for single-tenant deployments.

The runtime persists actions/traces/confirms under a team (``Action.team_id``
is NOT NULL). For a single-tenant test/demo deployment we seed one default
Organization + Team with a well-known UUID supplied via ``DEFAULT_TEAM_ID``.
"""

from __future__ import annotations

import uuid
from typing import Any

from core.models import Organization, Team


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


__all__ = ["ensure_default_team"]
