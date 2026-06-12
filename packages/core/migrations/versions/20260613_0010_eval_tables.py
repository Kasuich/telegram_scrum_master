"""eval harness tables

Revision ID: 20260613_0010
Revises: 20260612_0009
Create Date: 2026-06-13 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260613_0010"
down_revision: Union[str, None] = "20260612_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("config_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("git_commit", sa.String(64), nullable=True),
        sa.Column("generator_model", sa.String(128), nullable=True),
        sa.Column("judge_model", sa.String(128), nullable=True),
        sa.Column("agent_version", sa.String(64), nullable=True),
        sa.Column("total_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("generated_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("timeout_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pass_rate", sa.Float(), nullable=True),
        sa.Column("avg_latency_sec", sa.Float(), nullable=True),
        sa.Column("p95_latency_sec", sa.Float(), nullable=True),
        sa.Column("avg_agent_latency_sec", sa.Float(), nullable=True),
        sa.Column("p95_agent_latency_sec", sa.Float(), nullable=True),
        sa.Column("error_summary_json", postgresql.JSONB(), nullable=True),
        sa.Column("metrics_summary_json", postgresql.JSONB(), nullable=True),
    )
    op.create_index("idx_eval_runs_status", "eval_runs", ["status"])
    op.create_index("idx_eval_runs_created_at", "eval_runs", ["created_at"])

    op.create_table(
        "eval_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("suite", sa.String(64), nullable=False),
        sa.Column("difficulty", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("current_date", sa.String(16), nullable=True),
        sa.Column("generated_scenario_json", postgresql.JSONB(), nullable=True),
        sa.Column("user_text", sa.Text(), nullable=True),
        sa.Column("initial_state_json", postgresql.JSONB(), nullable=True),
        sa.Column("expected_operations_json", postgresql.JSONB(), nullable=True),
        sa.Column("forbidden_operations_json", postgresql.JSONB(), nullable=True),
        sa.Column("expected_final_state_json", postgresql.JSONB(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_eval_cases_run_id", "eval_cases", ["run_id"])
    op.create_index("idx_eval_cases_run_status", "eval_cases", ["run_id", "status"])
    op.create_index("idx_eval_cases_run_suite", "eval_cases", ["run_id", "suite"])

    op.create_table(
        "eval_case_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_cases.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("agent_raw_output_json", postgresql.JSONB(), nullable=True),
        sa.Column("agent_normalized_output_json", postgresql.JSONB(), nullable=True),
        sa.Column("final_fake_tracker_state_json", postgresql.JSONB(), nullable=True),
        sa.Column("deterministic_evaluation_json", postgresql.JSONB(), nullable=True),
        sa.Column("llm_judge_evaluation_json", postgresql.JSONB(), nullable=True),
        sa.Column("final_evaluation_json", postgresql.JSONB(), nullable=True),
        sa.Column("latency_sec", sa.Float(), nullable=True),
        sa.Column("agent_latency_sec", sa.Float(), nullable=True),
        sa.Column("judge_latency_sec", sa.Float(), nullable=True),
        sa.Column("agent_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agent_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("technical_error", sa.Text(), nullable=True),
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
    op.create_index("idx_eval_case_results_run_id", "eval_case_results", ["run_id"])
    op.create_index("idx_eval_case_results_case_id", "eval_case_results", ["case_id"])
    op.create_index("idx_eval_case_results_passed", "eval_case_results", ["run_id", "passed"])

    op.create_table(
        "eval_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("metric_name", sa.String(128), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("dimensions_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_eval_metrics_run_id", "eval_metrics", ["run_id"])

    op.create_table(
        "eval_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_cases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("level", sa.String(16), nullable=False, server_default="info"),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_eval_events_run_id", "eval_events", ["run_id"])
    op.create_index("idx_eval_events_case_id", "eval_events", ["case_id"])


def downgrade() -> None:
    op.drop_table("eval_events")
    op.drop_table("eval_metrics")
    op.drop_table("eval_case_results")
    op.drop_table("eval_cases")
    op.drop_table("eval_runs")
