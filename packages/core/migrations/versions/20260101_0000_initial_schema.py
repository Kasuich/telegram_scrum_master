"""initial schema

Revision ID: 20260101_0000
Revises:
Create Date: 2026-01-01 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260101_0000"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ---------------------------------------------------------------------------
# Custom enum types
# ---------------------------------------------------------------------------

risk_level_enum = sa.Enum("low", "medium", "high", name="risk_level_enum")
action_status_enum = sa.Enum("pending", "completed", "failed", name="action_status_enum")
confirm_status_enum = sa.Enum("pending", "approved", "rejected", name="confirm_status_enum")


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Create enum types first
    # ------------------------------------------------------------------
    risk_level_enum.create(op.get_bind(), checkfirst=True)
    action_status_enum.create(op.get_bind(), checkfirst=True)
    confirm_status_enum.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------
    # 1. organizations
    # ------------------------------------------------------------------
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
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
    )

    # ------------------------------------------------------------------
    # 2. users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="admin"),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
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
        sa.CheckConstraint("role IN ('dev', 'admin', 'user')", name="ck_users_role"),
    )

    op.create_index("idx_users_email", "users", ["email"])

    # ------------------------------------------------------------------
    # 3. console_sessions
    # ------------------------------------------------------------------
    op.create_table(
        "console_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
    )

    op.create_index("idx_console_sessions_token_hash", "console_sessions", ["token_hash"])
    op.create_index("idx_console_sessions_user_id", "console_sessions", ["user_id"])

    # ------------------------------------------------------------------
    # 4. teams
    # ------------------------------------------------------------------
    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "tracker_queue",
            sa.String(50),
            nullable=False,
            server_default="TEST",
        ),
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
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
    )

    # ------------------------------------------------------------------
    # 5. agent_specs
    # ------------------------------------------------------------------
    op.create_table(
        "agent_specs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column(
            "model",
            sa.String(100),
            nullable=False,
            server_default="gpt-oss-120b",
        ),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column(
            "tools",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "autonomy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
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
    )

    # ------------------------------------------------------------------
    # 6. agent_instances
    # ------------------------------------------------------------------
    op.create_table(
        "agent_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("spec_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "overlay",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
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
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["spec_id"],
            ["agent_specs.id"],
            ondelete="SET NULL",
        ),
    )

    # ------------------------------------------------------------------
    # 7. traces
    # ------------------------------------------------------------------
    op.create_table(
        "traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "steps",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_index("idx_traces_session_id", "traces", ["session_id"])

    # ------------------------------------------------------------------
    # 8. actions
    # ------------------------------------------------------------------
    op.create_table(
        "actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_instance_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column(
            "input",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "output",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "risk_level",
            sa.Enum("low", "medium", "high", name="risk_level_enum", create_type=False),
            nullable=False,
            server_default="low",
        ),
        sa.Column(
            "status",
            sa.Enum("pending", "completed", "failed", name="action_status_enum", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_instance_id"],
            ["agent_instances.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["trace_id"],
            ["traces.id"],
            ondelete="SET NULL",
        ),
    )

    op.create_index("idx_actions_team_id", "actions", ["team_id"])
    op.create_index("idx_actions_trace_id", "actions", ["trace_id"])
    op.create_index("idx_actions_created_at", "actions", ["created_at"])

    # ------------------------------------------------------------------
    # 9. confirms
    # ------------------------------------------------------------------
    op.create_table(
        "confirms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                name="confirm_status_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("answer", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["actions.id"],
            ondelete="CASCADE",
        ),
    )

    op.create_index("idx_confirms_action_id", "confirms", ["action_id"])

    # ------------------------------------------------------------------
    # 10. runtime_configs
    # ------------------------------------------------------------------
    op.create_table(
        "runtime_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("team_id", "key", name="uq_runtime_configs_team_key"),
    )

    op.create_index("idx_runtime_configs_team_id", "runtime_configs", ["team_id"])

    # ------------------------------------------------------------------
    # 11. scheduled_jobs
    # ------------------------------------------------------------------
    op.create_table(
        "scheduled_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("agent_instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("cron_expr", sa.String(100), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("max_runs", sa.Integer, nullable=True),
        sa.Column("run_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_run", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_instance_id"],
            ["agent_instances.id"],
            ondelete="CASCADE",
        ),
    )

    op.create_index("idx_scheduled_jobs_next_run", "scheduled_jobs", ["next_run"])

    # ------------------------------------------------------------------
    # 12. action_feedback
    # ------------------------------------------------------------------
    op.create_table(
        "action_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rating", sa.Integer, nullable=False),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_action_feedback_rating"),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["actions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
    )

    # ------------------------------------------------------------------
    # 13. langchain_checkpoints
    # ------------------------------------------------------------------
    op.create_table(
        "langchain_checkpoints",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.String(255), nullable=False),
        sa.Column(
            "checkpoint_ns",
            sa.String(255),
            nullable=False,
            server_default="",
        ),
        sa.Column("checkpoint_id", sa.String(255), nullable=False),
        sa.Column(
            "checkpoint_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            name="uq_langchain_checkpoints_thread_ns_id",
        ),
    )

    op.create_index(
        "idx_langchain_checkpoints_thread",
        "langchain_checkpoints",
        ["thread_id", "checkpoint_ns"],
    )


def downgrade() -> None:
    # Drop tables in reverse order (respecting FK dependencies)
    op.drop_index("idx_langchain_checkpoints_thread", table_name="langchain_checkpoints")
    op.drop_table("langchain_checkpoints")

    op.drop_table("action_feedback")

    op.drop_index("idx_scheduled_jobs_next_run", table_name="scheduled_jobs")
    op.drop_table("scheduled_jobs")

    op.drop_index("idx_runtime_configs_team_id", table_name="runtime_configs")
    op.drop_table("runtime_configs")

    op.drop_index("idx_confirms_action_id", table_name="confirms")
    op.drop_table("confirms")

    op.drop_index("idx_actions_created_at", table_name="actions")
    op.drop_index("idx_actions_trace_id", table_name="actions")
    op.drop_index("idx_actions_team_id", table_name="actions")
    op.drop_table("actions")

    op.drop_index("idx_traces_session_id", table_name="traces")
    op.drop_table("traces")

    op.drop_table("agent_instances")
    op.drop_table("agent_specs")
    op.drop_table("teams")
    op.drop_index("idx_console_sessions_user_id", table_name="console_sessions")
    op.drop_index("idx_console_sessions_token_hash", table_name="console_sessions")
    op.drop_table("console_sessions")
    op.drop_index("idx_users_email", table_name="users")
    op.drop_table("users")
    op.drop_table("organizations")

    # Drop enum types
    confirm_status_enum.drop(op.get_bind(), checkfirst=True)
    action_status_enum.drop(op.get_bind(), checkfirst=True)
    risk_level_enum.drop(op.get_bind(), checkfirst=True)
