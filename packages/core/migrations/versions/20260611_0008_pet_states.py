"""add pet_states for «Скрамик» leveling

Revision ID: 20260611_0008
Revises: 20260611_0007
Create Date: 2026-06-11 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260611_0008"
down_revision: Union[str, None] = "20260611_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pet_states",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("xp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("mood", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("streak_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evolution_tier", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("last_recalc_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("pet_states")
