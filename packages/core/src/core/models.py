"""
Database models for PM Agent Platform.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(AsyncAttrs, DeclarativeBase):
    """Base class for all database models."""

    type_annotation_map = {
        uuid.UUID: UUID(as_uuid=True),
        dict[str, Any]: JSONB,
        list[str]: JSONB,
    }


class Organization(Base):
    """Organization model - top-level entity."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    teams: Mapped[list[Team]] = relationship("Team", back_populates="organization")


class Team(Base):
    """Team model - belongs to organization."""

    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tracker_queue: Mapped[str] = mapped_column(String(50), nullable=False, default="TEST")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    organization: Mapped[Organization] = relationship("Organization", back_populates="teams")
    agent_instances: Mapped[list[AgentInstance]] = relationship(
        "AgentInstance", back_populates="team"
    )
    runtime_configs: Mapped[list[RuntimeConfigModel]] = relationship(
        "RuntimeConfigModel", back_populates="team"
    )
    actions: Mapped[list[Action]] = relationship("Action", back_populates="team")


class AgentSpec(Base):
    """Agent specification template."""

    __tablename__ = "agent_specs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False, default="yandexgpt")
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    tools: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    autonomy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    instances: Mapped[list[AgentInstance]] = relationship("AgentInstance", back_populates="spec")


class AgentInstance(Base):
    """Agent instance - specific config for a team."""

    __tablename__ = "agent_instances"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    spec_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_specs.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    overlay: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    team: Mapped[Team] = relationship("Team", back_populates="agent_instances")
    spec: Mapped[AgentSpec | None] = relationship("AgentSpec", back_populates="instances")
    scheduled_jobs: Mapped[list[ScheduledJob]] = relationship(
        "ScheduledJob", back_populates="agent_instance"
    )


class Action(Base):
    """Action performed by an agent."""

    __tablename__ = "actions"
    __table_args__ = (
        Index("idx_actions_team_id", "team_id"),
        Index("idx_actions_trace_id", "trace_id"),
        Index("idx_actions_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_instances.id", ondelete="SET NULL"),
        nullable=True,
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str] = mapped_column(
        SQLEnum("low", "medium", "high", name="risk_level_enum"),
        nullable=False,
        default="low",
    )
    status: Mapped[str] = mapped_column(
        SQLEnum("pending", "completed", "failed", name="action_status_enum"),
        nullable=False,
        default="pending",
    )
    trace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("traces.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    team: Mapped[Team] = relationship("Team", back_populates="actions")
    trace: Mapped[Trace | None] = relationship("Trace", back_populates="actions")
    confirms: Mapped[list[Confirm]] = relationship("Confirm", back_populates="action")
    feedback: Mapped[list[ActionFeedback]] = relationship("ActionFeedback", back_populates="action")


class Trace(Base):
    """Agent reasoning trace."""

    __tablename__ = "traces"
    __table_args__ = (Index("idx_traces_session_id", "session_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    actions: Mapped[list[Action]] = relationship("Action", back_populates="trace")


class Confirm(Base):
    """Confirmation request for agent action."""

    __tablename__ = "confirms"
    __table_args__ = (Index("idx_confirms_action_id", "action_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actions.id", ondelete="CASCADE"),
        nullable=False,
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        SQLEnum("pending", "approved", "rejected", name="confirm_status_enum"),
        nullable=False,
        default="pending",
    )
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    action: Mapped[Action] = relationship("Action", back_populates="confirms")


class RuntimeConfigModel(Base):
    """Runtime configuration per team."""

    __tablename__ = "runtime_configs"
    __table_args__ = (
        Index("idx_runtime_configs_team_id", "team_id"),
        UniqueConstraint("team_id", "key", name="uq_runtime_configs_team_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    team: Mapped[Team] = relationship("Team", back_populates="runtime_configs")


class ScheduledJob(Base):
    """Scheduled job for agent self-scheduling."""

    __tablename__ = "scheduled_jobs"
    __table_args__ = (Index("idx_scheduled_jobs_next_run", "next_run"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    agent_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    cron_expr: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    max_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_run: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    agent_instance: Mapped[AgentInstance] = relationship(
        "AgentInstance", back_populates="scheduled_jobs"
    )


class ActionFeedback(Base):
    """User feedback on agent actions."""

    __tablename__ = "action_feedback"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_action_feedback_rating"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    action: Mapped[Action] = relationship("Action", back_populates="feedback")


__all__ = [
    "Base",
    "Organization",
    "Team",
    "AgentSpec",
    "AgentInstance",
    "Action",
    "Trace",
    "Confirm",
    "RuntimeConfigModel",
    "ScheduledJob",
    "ActionFeedback",
]
