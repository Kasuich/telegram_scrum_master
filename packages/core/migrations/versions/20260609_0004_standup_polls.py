"""add Telegram standup poll state

Revision ID: 20260609_0004
Revises: 20260609_0003
Create Date: 2026-06-09 00:10:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260609_0004"
down_revision: Union[str, None] = "20260609_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_standup_polls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tracker_login", sa.String(255), nullable=False),
        sa.Column("board_id", sa.String(64), nullable=True),
        sa.Column("board_name", sa.String(255), nullable=True),
        sa.Column("local_hour", sa.String(32), nullable=False),
        sa.Column("issues_json", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("applied_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "team_id",
            "telegram_user_id",
            "local_hour",
            name="uq_telegram_standup_polls_team_user_hour",
        ),
    )
    op.create_index(
        "idx_telegram_standup_polls_team_hour",
        "telegram_standup_polls",
        ["team_id", "local_hour"],
    )
    op.create_index(
        "idx_telegram_standup_polls_user_status",
        "telegram_standup_polls",
        ["team_id", "telegram_user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_telegram_standup_polls_user_status", table_name="telegram_standup_polls")
    op.drop_index("idx_telegram_standup_polls_team_hour", table_name="telegram_standup_polls")
    op.drop_table("telegram_standup_polls")
