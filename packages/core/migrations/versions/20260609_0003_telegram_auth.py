"""add Telegram onboarding, Tracker membership and UI login challenges

Revision ID: 20260609_0003
Revises: 20260606_0002
Create Date: 2026-06-09 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260609_0003"
down_revision: Union[str, None] = "20260606_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "team_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tracker_login", sa.String(255), nullable=False),
        sa.Column("tracker_uid", sa.String(255), nullable=True),
        sa.Column("tracker_display_name", sa.String(255), nullable=True),
        sa.Column(
            "tracker_match_status",
            sa.String(32),
            nullable=False,
            server_default="confirmed",
        ),
        sa.Column("default_board_id", sa.String(64), nullable=True),
        sa.Column("role", sa.String(32), nullable=False, server_default="user"),
        sa.Column("settings_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_memberships_team_user"),
        sa.UniqueConstraint(
            "team_id",
            "tracker_login",
            name="uq_team_memberships_team_tracker_login",
        ),
    )
    op.create_index("idx_team_memberships_team_id", "team_memberships", ["team_id"])

    op.create_table(
        "telegram_onboarding_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("step_key", sa.String(64), nullable=False, server_default="tracker_login"),
        sa.Column("answers_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["telegram_installations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_telegram_onboarding_team_user_status",
        "telegram_onboarding_sessions",
        ["team_id", "telegram_user_id", "status"],
    )

    op.create_table(
        "login_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_ip", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["telegram_installations.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_login_challenges_user_status",
        "login_challenges",
        ["user_id", "status"],
    )
    op.create_index(
        "idx_login_challenges_expires_at",
        "login_challenges",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_login_challenges_expires_at", table_name="login_challenges")
    op.drop_index("idx_login_challenges_user_status", table_name="login_challenges")
    op.drop_table("login_challenges")
    op.drop_index(
        "idx_telegram_onboarding_team_user_status",
        table_name="telegram_onboarding_sessions",
    )
    op.drop_table("telegram_onboarding_sessions")
    op.drop_index("idx_team_memberships_team_id", table_name="team_memberships")
    op.drop_table("team_memberships")
