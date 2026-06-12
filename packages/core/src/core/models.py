"""
Database models for PM Agent Platform.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
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


class User(Base):
    """Console user for the GUI control plane."""

    __tablename__ = "users"
    __table_args__ = (
        Index("idx_users_email", "email"),
        CheckConstraint("role IN ('dev', 'admin', 'user')", name="ck_users_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="admin")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    sessions: Mapped[list[ConsoleSession]] = relationship(
        "ConsoleSession",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    feedback: Mapped[list[ActionFeedback]] = relationship(
        "ActionFeedback",
        back_populates="user",
    )
    team_memberships: Mapped[list[TeamMembership]] = relationship(
        "TeamMembership",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    profile: Mapped[UserProfile | None] = relationship(
        "UserProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )


class UserProfile(Base):
    """Social-style profile for a console user.

    Public fields (avatar, title, bio, contacts) are visible to other users;
    ``private_json`` holds personal info visible only to the owner.
    """

    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    avatar_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    contacts_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    private_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped[User] = relationship("User", back_populates="profile")


class PetState(Base):
    """Persisted «Скрамик» state per user (leveling/mood snapshot)."""

    __tablename__ = "pet_states"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    species_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    xp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    mood: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    streak_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evolution_tier: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_recalc_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class PetBattle(Base):
    """One «битва скрамиков» outcome (royale or 1-on-1 duel) for the leaderboard."""

    __tablename__ = "pet_battles"
    __table_args__ = (
        Index("idx_pet_battles_team_id", "team_id"),
        Index("idx_pet_battles_winner", "winner_user_id"),
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
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="duel")  # duel | royale
    attacker_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    defender_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    winner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    log_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class ConsoleSession(Base):
    """Cookie-backed session for the GUI control plane."""

    __tablename__ = "console_sessions"
    __table_args__ = (
        Index("idx_console_sessions_token_hash", "token_hash"),
        Index("idx_console_sessions_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    user: Mapped[User] = relationship("User", back_populates="sessions")


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
    telegram_installations: Mapped[list[TelegramInstallation]] = relationship(
        "TelegramInstallation",
        back_populates="team",
    )
    telegram_messages: Mapped[list[TelegramMessage]] = relationship(
        "TelegramMessage",
        back_populates="team",
    )
    telegram_outbox: Mapped[list[TelegramOutbox]] = relationship(
        "TelegramOutbox",
        back_populates="team",
    )
    telegram_user_links: Mapped[list[TelegramUserLink]] = relationship(
        "TelegramUserLink",
        back_populates="team",
    )
    telegram_notification_preferences: Mapped[list[TelegramNotificationPreference]] = relationship(
        "TelegramNotificationPreference",
        back_populates="team",
    )
    runtime_configs: Mapped[list[RuntimeConfigModel]] = relationship(
        "RuntimeConfigModel", back_populates="team"
    )
    actions: Mapped[list[Action]] = relationship("Action", back_populates="team")
    meetings: Mapped[list[Meeting]] = relationship("Meeting", back_populates="team")
    memberships: Mapped[list[TeamMembership]] = relationship(
        "TeamMembership",
        back_populates="team",
        cascade="all, delete-orphan",
    )


class TeamMembership(Base):
    """Confirmed mapping between an internal user and a Tracker team member."""

    __tablename__ = "team_memberships"
    __table_args__ = (
        Index("idx_team_memberships_team_id", "team_id"),
        UniqueConstraint("team_id", "user_id", name="uq_team_memberships_team_user"),
        UniqueConstraint(
            "team_id",
            "tracker_login",
            name="uq_team_memberships_team_tracker_login",
        ),
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
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    tracker_login: Mapped[str] = mapped_column(String(255), nullable=False)
    tracker_uid: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tracker_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tracker_match_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="confirmed",
    )
    default_board_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    team: Mapped[Team] = relationship("Team", back_populates="memberships")
    user: Mapped[User] = relationship("User", back_populates="team_memberships")


class AgentSpec(Base):
    """Agent specification template."""

    __tablename__ = "agent_specs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False, default="gpt-oss-120b")
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


class TelegramInstallation(Base):
    """Logical Telegram bot installation for a team."""

    __tablename__ = "telegram_installations"
    __table_args__ = (
        Index("idx_telegram_installations_team_id", "team_id"),
        UniqueConstraint("team_id", "alias", name="uq_telegram_installations_team_alias"),
        UniqueConstraint(
            "external_bot_id",
            name="uq_telegram_installations_external_bot_id",
        ),
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
    alias: Mapped[str] = mapped_column(String(100), nullable=False)
    external_bot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="workspace_bot")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    team: Mapped[Team] = relationship("Team", back_populates="telegram_installations")
    chats: Mapped[list[TelegramChat]] = relationship(
        "TelegramChat",
        back_populates="installation",
        cascade="all, delete-orphan",
    )
    updates: Mapped[list[TelegramUpdate]] = relationship(
        "TelegramUpdate",
        back_populates="installation",
        cascade="all, delete-orphan",
    )
    messages: Mapped[list[TelegramMessage]] = relationship(
        "TelegramMessage",
        back_populates="installation",
    )
    outbox_entries: Mapped[list[TelegramOutbox]] = relationship(
        "TelegramOutbox",
        back_populates="installation",
    )
    callback_tokens: Mapped[list[TelegramCallbackToken]] = relationship(
        "TelegramCallbackToken",
        back_populates="installation",
    )
    user_links: Mapped[list[TelegramUserLink]] = relationship(
        "TelegramUserLink",
        back_populates="installation",
    )
    business_connections: Mapped[list[TelegramBusinessConnection]] = relationship(
        "TelegramBusinessConnection",
        back_populates="installation",
    )


class TelegramChat(Base):
    """Telegram chat binding for an installation."""

    __tablename__ = "telegram_chats"
    __table_args__ = (
        Index("idx_telegram_chats_installation_id", "installation_id"),
        UniqueConstraint(
            "installation_id",
            "external_chat_id",
            name="uq_telegram_chats_installation_chat",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ingest_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="disabled")
    access_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="workspace_bot")
    send_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    installation: Mapped[TelegramInstallation] = relationship(
        "TelegramInstallation",
        back_populates="chats",
    )
    messages: Mapped[list[TelegramMessage]] = relationship(
        "TelegramMessage",
        back_populates="chat",
    )
    outbox_entries: Mapped[list[TelegramOutbox]] = relationship(
        "TelegramOutbox",
        back_populates="chat",
    )
    import_jobs: Mapped[list[TelegramImportJob]] = relationship(
        "TelegramImportJob",
        back_populates="chat",
    )


class TelegramUser(Base):
    """Telegram identity known to the platform."""

    __tablename__ = "telegram_users"
    __table_args__ = (
        Index("idx_telegram_users_external_user_id", "external_user_id"),
        UniqueConstraint("external_user_id", name="uq_telegram_users_external_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    external_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_bot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    links: Mapped[list[TelegramUserLink]] = relationship(
        "TelegramUserLink",
        back_populates="telegram_user",
    )
    messages: Mapped[list[TelegramMessage]] = relationship(
        "TelegramMessage",
        back_populates="telegram_user",
    )
    callback_tokens: Mapped[list[TelegramCallbackToken]] = relationship(
        "TelegramCallbackToken",
        back_populates="telegram_user",
    )
    notification_preferences: Mapped[list[TelegramNotificationPreference]] = relationship(
        "TelegramNotificationPreference",
        back_populates="telegram_user",
    )
    business_connections: Mapped[list[TelegramBusinessConnection]] = relationship(
        "TelegramBusinessConnection",
        back_populates="telegram_user",
    )
    onboarding_sessions: Mapped[list[TelegramOnboardingSession]] = relationship(
        "TelegramOnboardingSession",
        back_populates="telegram_user",
    )


class TelegramUserLink(Base):
    """Team-scoped link between Telegram identity and internal user."""

    __tablename__ = "telegram_user_links"
    __table_args__ = (
        Index("idx_telegram_user_links_team_id", "team_id"),
        UniqueConstraint(
            "team_id",
            "telegram_user_id",
            name="uq_telegram_user_links_team_telegram_user",
        ),
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
    installation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_installations.id", ondelete="SET NULL"),
        nullable=True,
    )
    telegram_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    team: Mapped[Team] = relationship("Team", back_populates="telegram_user_links")
    installation: Mapped[TelegramInstallation | None] = relationship(
        "TelegramInstallation",
        back_populates="user_links",
    )
    telegram_user: Mapped[TelegramUser] = relationship(
        "TelegramUser",
        back_populates="links",
    )
    user: Mapped[User | None] = relationship("User")


class TelegramOnboardingSession(Base):
    """Deterministic Telegram onboarding state for Tracker identity matching."""

    __tablename__ = "telegram_onboarding_sessions"
    __table_args__ = (
        Index(
            "idx_telegram_onboarding_team_user_status",
            "team_id",
            "telegram_user_id",
            "status",
        ),
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
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    telegram_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    step_key: Mapped[str] = mapped_column(String(64), nullable=False, default="tracker_login")
    answers_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    telegram_user: Mapped[TelegramUser] = relationship(
        "TelegramUser",
        back_populates="onboarding_sessions",
    )


class TelegramStandupPoll(Base):
    """Per-user hourly standup poll state for deterministic Telegram replies."""

    __tablename__ = "telegram_standup_polls"
    __table_args__ = (
        Index("idx_telegram_standup_polls_team_hour", "team_id", "local_hour"),
        Index(
            "idx_telegram_standup_polls_user_status",
            "team_id",
            "telegram_user_id",
            "status",
        ),
        UniqueConstraint(
            "team_id",
            "telegram_user_id",
            "local_hour",
            name="uq_telegram_standup_polls_team_user_hour",
        ),
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
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    telegram_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    tracker_login: Mapped[str] = mapped_column(String(255), nullable=False)
    board_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    board_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    local_hour: Mapped[str] = mapped_column(String(32), nullable=False)
    issues_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    team: Mapped[Team] = relationship("Team")
    installation: Mapped[TelegramInstallation] = relationship("TelegramInstallation")
    telegram_user: Mapped[TelegramUser] = relationship("TelegramUser")
    user: Mapped[User] = relationship("User")


class LoginChallenge(Base):
    """Short-lived one-time code challenge that creates a console session."""

    __tablename__ = "login_challenges"
    __table_args__ = (
        Index("idx_login_challenges_user_status", "user_id", "status"),
        Index("idx_login_challenges_expires_at", "expires_at"),
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
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    telegram_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    request_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class TelegramBusinessConnection(Base):
    """Telegram secretary/business connection."""

    __tablename__ = "telegram_business_connections"
    __table_args__ = (
        Index("idx_telegram_business_connections_team_id", "team_id"),
        UniqueConstraint(
            "business_connection_id",
            name="uq_telegram_business_connections_external_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    telegram_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    business_connection_id: Mapped[str] = mapped_column(String(128), nullable=False)
    can_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    selected_chat_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    installation: Mapped[TelegramInstallation] = relationship(
        "TelegramInstallation",
        back_populates="business_connections",
    )
    telegram_user: Mapped[TelegramUser] = relationship(
        "TelegramUser",
        back_populates="business_connections",
    )
    messages: Mapped[list[TelegramMessage]] = relationship(
        "TelegramMessage",
        back_populates="business_connection",
    )
    outbox_entries: Mapped[list[TelegramOutbox]] = relationship(
        "TelegramOutbox",
        back_populates="business_connection",
    )


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


class TelegramUpdate(Base):
    """Raw Telegram update persisted for durable ingest."""

    __tablename__ = "telegram_updates"
    __table_args__ = (
        Index("idx_telegram_updates_installation_id", "installation_id"),
        UniqueConstraint(
            "installation_id",
            "update_id",
            name="uq_telegram_updates_installation_update_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    payload_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    installation: Mapped[TelegramInstallation] = relationship(
        "TelegramInstallation",
        back_populates="updates",
    )
    messages: Mapped[list[TelegramMessage]] = relationship(
        "TelegramMessage",
        back_populates="raw_update",
    )


class TelegramMessage(Base):
    """Normalized Telegram message corpus."""

    __tablename__ = "telegram_messages"
    __table_args__ = (
        Index("idx_telegram_messages_team_id", "team_id"),
        Index("idx_telegram_messages_chat_sent_at", "external_chat_id", "sent_at"),
        Index("idx_telegram_messages_thread_id", "external_thread_id"),
        UniqueConstraint(
            "installation_id",
            "external_chat_id",
            "external_message_id",
            name="uq_telegram_messages_installation_chat_message",
        ),
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
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_chats.id", ondelete="SET NULL"),
        nullable=True,
    )
    telegram_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    business_connection_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_business_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    raw_update_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_updates.id", ondelete="SET NULL"),
        nullable=True,
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="inbound")
    access_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="workspace_bot")
    external_chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reply_to_external_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    import_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    media_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    team: Mapped[Team] = relationship("Team", back_populates="telegram_messages")
    installation: Mapped[TelegramInstallation] = relationship(
        "TelegramInstallation",
        back_populates="messages",
    )
    chat: Mapped[TelegramChat | None] = relationship(
        "TelegramChat",
        back_populates="messages",
    )
    telegram_user: Mapped[TelegramUser | None] = relationship(
        "TelegramUser",
        back_populates="messages",
    )
    business_connection: Mapped[TelegramBusinessConnection | None] = relationship(
        "TelegramBusinessConnection",
        back_populates="messages",
    )
    raw_update: Mapped[TelegramUpdate | None] = relationship(
        "TelegramUpdate",
        back_populates="messages",
    )


class TelegramOutbox(Base):
    """Outgoing Telegram deliveries leased by the gateway."""

    __tablename__ = "telegram_outbox"
    __table_args__ = (
        Index("idx_telegram_outbox_status_next_attempt", "status", "next_attempt_at"),
        Index("idx_telegram_outbox_team_id", "team_id"),
        UniqueConstraint("team_id", "dedupe_key", name="uq_telegram_outbox_team_dedupe_key"),
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
    installation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_installations.id", ondelete="SET NULL"),
        nullable=True,
    )
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_chats.id", ondelete="SET NULL"),
        nullable=True,
    )
    business_connection_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_business_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="agent_reply")
    target_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    team: Mapped[Team] = relationship("Team", back_populates="telegram_outbox")
    installation: Mapped[TelegramInstallation | None] = relationship(
        "TelegramInstallation",
        back_populates="outbox_entries",
    )
    chat: Mapped[TelegramChat | None] = relationship(
        "TelegramChat",
        back_populates="outbox_entries",
    )
    business_connection: Mapped[TelegramBusinessConnection | None] = relationship(
        "TelegramBusinessConnection",
        back_populates="outbox_entries",
    )


class TelegramCallbackToken(Base):
    """Opaque callback tokens for Telegram interactive actions."""

    __tablename__ = "telegram_callback_tokens"
    __table_args__ = (
        Index("idx_telegram_callback_tokens_confirm_id", "confirm_id"),
        UniqueConstraint(
            "token_hash",
            name="uq_telegram_callback_tokens_token_hash",
        ),
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
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    telegram_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    confirm_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("confirms.id", ondelete="SET NULL"),
        nullable=True,
    )
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    target_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    installation: Mapped[TelegramInstallation] = relationship(
        "TelegramInstallation",
        back_populates="callback_tokens",
    )
    telegram_user: Mapped[TelegramUser | None] = relationship(
        "TelegramUser",
        back_populates="callback_tokens",
    )
    confirm: Mapped[Confirm | None] = relationship("Confirm")


class TelegramImportJob(Base):
    """Import job for historical Telegram data."""

    __tablename__ = "telegram_import_jobs"
    __table_args__ = (Index("idx_telegram_import_jobs_team_status", "team_id", "status"),)

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
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    chat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_chats.id", ondelete="CASCADE"),
        nullable=False,
    )
    import_source: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="telegram_desktop",
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    total_messages: Mapped[int] = mapped_column(default=0)
    processed_messages: Mapped[int] = mapped_column(default=0)
    created_messages: Mapped[int] = mapped_column(default=0)
    skipped_messages: Mapped[int] = mapped_column(default=0)
    failed_messages: Mapped[int] = mapped_column(default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    team: Mapped[Team] = relationship("Team")
    installation: Mapped[TelegramInstallation] = relationship("TelegramInstallation")
    chat: Mapped[TelegramChat] = relationship("TelegramChat", back_populates="import_jobs")


class TelegramNotificationPreference(Base):
    """Notification preferences for Telegram delivery."""

    __tablename__ = "telegram_notification_preferences"
    __table_args__ = (
        Index("idx_telegram_notification_preferences_team_id", "team_id"),
        UniqueConstraint(
            "team_id",
            "telegram_user_id",
            "category",
            name="uq_telegram_notification_preferences_team_user_category",
        ),
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
    telegram_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quiet_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    digest_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    team: Mapped[Team] = relationship(
        "Team",
        back_populates="telegram_notification_preferences",
    )
    telegram_user: Mapped[TelegramUser] = relationship(
        "TelegramUser",
        back_populates="notification_preferences",
    )


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
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    action: Mapped[Action] = relationship("Action", back_populates="feedback")
    user: Mapped[User | None] = relationship("User", back_populates="feedback")


class Meeting(Base):
    """Captured Telemost meeting lifecycle state."""

    __tablename__ = "meetings"
    __table_args__ = (
        Index("idx_meetings_team_status", "team_id", "status"),
        Index("idx_meetings_scheduled_at", "scheduled_at"),
        CheckConstraint(
            "status IN ("
            "'scheduled', 'joining', 'waiting_room', 'recording', 'transcribing', "
            "'ready', 'failed', 'skipped'"
            ")",
            name="ck_meetings_status",
        ),
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
    telemost_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="ru-RU")
    consent_ack: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recording_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    team: Mapped[Team] = relationship("Team", back_populates="meetings")
    artifacts: Mapped[list[MeetingArtifact]] = relationship(
        "MeetingArtifact",
        back_populates="meeting",
        cascade="all, delete-orphan",
    )
    transcript: Mapped[Transcript | None] = relationship(
        "Transcript",
        back_populates="meeting",
        cascade="all, delete-orphan",
        uselist=False,
    )


class MeetingArtifact(Base):
    """Object-storage artifact produced by a captured meeting."""

    __tablename__ = "meeting_artifacts"
    __table_args__ = (
        Index("idx_meeting_artifacts_meeting_id", "meeting_id"),
        CheckConstraint(
            "kind IN ('recording', 'audio', 'screenshot', 'log', "
            "'transcript', 'transcript_json', 'summary')",
            name="ck_meeting_artifacts_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    meeting: Mapped[Meeting] = relationship("Meeting", back_populates="artifacts")


class Transcript(Base):
    """Speaker-attributed transcript for a captured meeting."""

    __tablename__ = "transcripts"
    __table_args__ = (
        Index("idx_transcripts_meeting_id", "meeting_id"),
        UniqueConstraint("meeting_id", name="uq_transcripts_meeting_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="speechkit")
    segments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    participants_observed: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    meeting: Mapped[Meeting] = relationship("Meeting", back_populates="transcript")


__all__ = [
    "Base",
    "Organization",
    "User",
    "ConsoleSession",
    "Team",
    "TeamMembership",
    "AgentSpec",
    "TelegramInstallation",
    "TelegramChat",
    "TelegramUser",
    "TelegramUserLink",
    "TelegramBusinessConnection",
    "AgentInstance",
    "Action",
    "Trace",
    "Confirm",
    "TelegramUpdate",
    "TelegramMessage",
    "TelegramOutbox",
    "TelegramCallbackToken",
    "TelegramImportJob",
    "TelegramNotificationPreference",
    "TelegramOnboardingSession",
    "TelegramStandupPoll",
    "LoginChallenge",
    "RuntimeConfigModel",
    "ScheduledJob",
    "ActionFeedback",
    "Meeting",
    "MeetingArtifact",
    "Transcript",
]
