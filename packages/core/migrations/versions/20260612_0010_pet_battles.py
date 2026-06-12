"""add pet_battles table for «Битва скрамиков» results + duel leaderboard

Revision ID: 20260612_0010
Revises: 20260612_0009
Create Date: 2026-06-12 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260612_0010"
down_revision: Union[str, None] = "20260612_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pet_battles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="duel"),
        sa.Column(
            "attacker_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "defender_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "winner_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("log_json", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_pet_battles_team_id", "pet_battles", ["team_id"])
    op.create_index("idx_pet_battles_winner", "pet_battles", ["winner_user_id"])


def downgrade() -> None:
    op.drop_index("idx_pet_battles_winner", table_name="pet_battles")
    op.drop_index("idx_pet_battles_team_id", table_name="pet_battles")
    op.drop_table("pet_battles")
