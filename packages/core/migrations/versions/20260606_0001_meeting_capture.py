"""meeting capture tables

Revision ID: 20260606_0001
Revises: 20260101_0000
Create Date: 2026-06-06 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260606_0001"
down_revision: Union[str, None] = "20260101_0000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "meetings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telemost_url", sa.Text, nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="scheduled"),
        sa.Column("language", sa.String(16), nullable=False, server_default="ru-RU"),
        sa.Column("consent_ack", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recording_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ("
            "'scheduled', 'joining', 'waiting_room', 'recording', 'transcribing', "
            "'ready', 'failed', 'skipped'"
            ")",
            name="ck_meetings_status",
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_meetings_team_status", "meetings", ["team_id", "status"])
    op.create_index("idx_meetings_scheduled_at", "meetings", ["scheduled_at"])

    op.create_table(
        "meeting_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("object_key", sa.Text, nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('recording', 'audio', 'screenshot', 'log')",
            name="ck_meeting_artifacts_kind",
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_meeting_artifacts_meeting_id",
        "meeting_artifacts",
        ["meeting_id"],
    )

    op.create_table(
        "transcripts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(64), nullable=False, server_default="speechkit"),
        sa.Column(
            "segments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "participants_observed",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("meeting_id", name="uq_transcripts_meeting_id"),
    )
    op.create_index("idx_transcripts_meeting_id", "transcripts", ["meeting_id"])


def downgrade() -> None:
    op.drop_index("idx_transcripts_meeting_id", table_name="transcripts")
    op.drop_table("transcripts")
    op.drop_index("idx_meeting_artifacts_meeting_id", table_name="meeting_artifacts")
    op.drop_table("meeting_artifacts")
    op.drop_index("idx_meetings_scheduled_at", table_name="meetings")
    op.drop_index("idx_meetings_team_status", table_name="meetings")
    op.drop_table("meetings")
