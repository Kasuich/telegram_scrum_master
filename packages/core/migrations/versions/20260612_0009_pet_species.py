"""add species_id to pet_states for «Скрамик» collectible species

Revision ID: 20260612_0009
Revises: 20260611_0008
Create Date: 2026-06-12 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260612_0009"
down_revision: Union[str, None] = "20260611_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "pet_states",
        sa.Column("species_id", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pet_states", "species_id")
