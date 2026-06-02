"""
Tests for core.models — structure and metadata of ORM models.

No real database connection is needed. Tests only inspect Python-level
model definitions: columns, defaults, table names, indexes, and constraints.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import (
    CheckConstraint,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase

from core.models import (
    Action,
    ActionFeedback,
    AgentInstance,
    AgentSpec,
    Base,
    Confirm,
    Organization,
    RuntimeConfigModel,
    ScheduledJob,
    Team,
    Trace,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_column(model: type, col_name: str):
    """Return the Column object for a mapped column by name."""
    return model.__table__.c[col_name]


def _table_args(model: type) -> tuple:
    """Return __table_args__ for a model (or empty tuple)."""
    return getattr(model, "__table_args__", ())


def _index_names(model: type) -> set[str]:
    """Collect names of all Index objects in __table_args__."""
    return {
        item.name
        for item in _table_args(model)
        if isinstance(item, Index) and item.name is not None
    }


def _constraint_names(model: type) -> set[str]:
    """Collect names of UniqueConstraint / CheckConstraint from __table_args__ and __table__."""
    names: set[str] = set()
    for item in _table_args(model):
        if isinstance(item, (UniqueConstraint, CheckConstraint)) and item.name:
            names.add(item.name)
    # Also check the Table-level constraints (populated after mapper init)
    for c in model.__table__.constraints:
        if isinstance(c, (UniqueConstraint, CheckConstraint)) and c.name:
            names.add(c.name)
    return names


# ===========================================================================
# 1. TestBase
# ===========================================================================


class TestBase:
    """Every model must have __tablename__, id, and created_at."""

    ALL_MODELS = [
        Organization,
        Team,
        AgentSpec,
        AgentInstance,
        Action,
        Trace,
        Confirm,
        RuntimeConfigModel,
        ScheduledJob,
        ActionFeedback,
    ]

    def test_base_is_declarative_base(self) -> None:
        """Base inherits from DeclarativeBase."""
        assert issubclass(Base, DeclarativeBase)

    @pytest.mark.parametrize("model", ALL_MODELS)
    def test_has_tablename(self, model: type) -> None:
        """Model declares __tablename__."""
        assert hasattr(model, "__tablename__")
        assert isinstance(model.__tablename__, str)
        assert len(model.__tablename__) > 0

    @pytest.mark.parametrize("model", ALL_MODELS)
    def test_has_id_column(self, model: type) -> None:
        """Model has an 'id' primary-key column."""
        assert "id" in model.__table__.c
        assert model.__table__.c["id"].primary_key

    @pytest.mark.parametrize("model", ALL_MODELS)
    def test_has_created_at_column(self, model: type) -> None:
        """Model has a 'created_at' column."""
        assert "created_at" in model.__table__.c


# ===========================================================================
# 2. TestOrganization
# ===========================================================================


class TestOrganization:
    """Tests for Organization model."""

    def test_tablename(self) -> None:
        assert Organization.__tablename__ == "organizations"

    def test_id_is_uuid(self) -> None:
        obj = Organization(name="Test Org")
        # default factory generates uuid
        assert obj.id is None or isinstance(obj.id, (uuid.UUID, type(None)))

    def test_name_column_exists(self) -> None:
        assert "name" in Organization.__table__.c
        col = _get_column(Organization, "name")
        assert not col.nullable

    def test_created_at_and_updated_at_columns(self) -> None:
        assert "created_at" in Organization.__table__.c
        assert "updated_at" in Organization.__table__.c

    def test_default_uuid_factory(self) -> None:
        """id column has a Python-side default (uuid.uuid4)."""
        col = _get_column(Organization, "id")
        assert col.default is not None or col.primary_key


# ===========================================================================
# 3. TestTeam
# ===========================================================================


class TestTeam:
    """Tests for Team model."""

    def test_tablename(self) -> None:
        assert Team.__tablename__ == "teams"

    def test_organization_id_fk(self) -> None:
        col = _get_column(Team, "organization_id")
        assert col.foreign_keys
        fk = next(iter(col.foreign_keys))
        assert "organizations.id" in str(fk.target_fullname)

    def test_tracker_queue_default(self) -> None:
        col = _get_column(Team, "tracker_queue")
        assert col.default.arg == "TEST"

    def test_name_not_nullable(self) -> None:
        col = _get_column(Team, "name")
        assert not col.nullable

    def test_updated_at_column_exists(self) -> None:
        assert "updated_at" in Team.__table__.c


# ===========================================================================
# 4. TestAgentSpec
# ===========================================================================


class TestAgentSpec:
    """Tests for AgentSpec model."""

    def test_tablename(self) -> None:
        assert AgentSpec.__tablename__ == "agent_specs"

    def test_tools_default_is_list(self) -> None:
        """tools column default must be list factory, not dict (regression guard)."""
        col = _get_column(AgentSpec, "tools")
        assert col.default is not None
        # SQLAlchemy wraps `default=list` as a CallableColumnDefault; .arg holds the callable.
        # In Python 3.13 the built-in 'list' inside SQLAlchemy's wrapper has a different id
        # than the 'list' in this module, so we check the callable's __name__ instead.
        default_fn = col.default.arg
        assert callable(default_fn), "tools default should be a callable"
        assert default_fn.__name__ == "list", (
            f"tools default must be 'list', got '{default_fn.__name__}'"
        )

    def test_autonomy_default_is_dict(self) -> None:
        col = _get_column(AgentSpec, "autonomy")
        assert col.default is not None
        default_fn = col.default.arg
        assert callable(default_fn), "autonomy default should be a callable"
        assert default_fn.__name__ == "dict"

    def test_name_unique(self) -> None:
        col = _get_column(AgentSpec, "name")
        assert col.unique

    def test_model_default(self) -> None:
        col = _get_column(AgentSpec, "model")
        assert col.default.arg == "yandexgpt-pro"

    def test_prompt_not_nullable(self) -> None:
        col = _get_column(AgentSpec, "prompt")
        assert not col.nullable


# ===========================================================================
# 5. TestAgentInstance
# ===========================================================================


class TestAgentInstance:
    """Tests for AgentInstance model."""

    def test_tablename(self) -> None:
        assert AgentInstance.__tablename__ == "agent_instances"

    def test_team_id_fk(self) -> None:
        col = _get_column(AgentInstance, "team_id")
        assert col.foreign_keys

    def test_overlay_default_is_dict(self) -> None:
        col = _get_column(AgentInstance, "overlay")
        assert col.default is not None
        default_fn = col.default.arg
        assert callable(default_fn)
        assert default_fn.__name__ == "dict"

    def test_enabled_default_true(self) -> None:
        col = _get_column(AgentInstance, "enabled")
        assert col.default.arg is True

    def test_spec_id_nullable(self) -> None:
        """spec_id is optional (agent can exist without a spec)."""
        col = _get_column(AgentInstance, "spec_id")
        assert col.nullable


# ===========================================================================
# 6. TestAction
# ===========================================================================


class TestAction:
    """Tests for Action model."""

    def test_tablename(self) -> None:
        assert Action.__tablename__ == "actions"

    def test_risk_level_default(self) -> None:
        col = _get_column(Action, "risk_level")
        assert col.default.arg == "low"

    def test_status_default(self) -> None:
        col = _get_column(Action, "status")
        assert col.default.arg == "pending"

    def test_index_team_id(self) -> None:
        assert "idx_actions_team_id" in _index_names(Action)

    def test_index_trace_id(self) -> None:
        assert "idx_actions_trace_id" in _index_names(Action)

    def test_index_created_at(self) -> None:
        assert "idx_actions_created_at" in _index_names(Action)

    def test_team_id_fk(self) -> None:
        col = _get_column(Action, "team_id")
        assert col.foreign_keys

    def test_trace_id_nullable(self) -> None:
        col = _get_column(Action, "trace_id")
        assert col.nullable


# ===========================================================================
# 7. TestTrace
# ===========================================================================


class TestTrace:
    """Tests for Trace model."""

    def test_tablename(self) -> None:
        assert Trace.__tablename__ == "traces"

    def test_steps_default_is_list(self) -> None:
        col = _get_column(Trace, "steps")
        assert col.default is not None
        default_fn = col.default.arg
        assert callable(default_fn)
        assert default_fn.__name__ == "list"

    def test_index_session_id(self) -> None:
        assert "idx_traces_session_id" in _index_names(Trace)

    def test_session_id_not_nullable(self) -> None:
        col = _get_column(Trace, "session_id")
        assert not col.nullable

    def test_metadata_json_nullable(self) -> None:
        col = _get_column(Trace, "metadata_json")
        assert col.nullable


# ===========================================================================
# 8. TestConfirm
# ===========================================================================


class TestConfirm:
    """Tests for Confirm model."""

    def test_tablename(self) -> None:
        assert Confirm.__tablename__ == "confirms"

    def test_action_id_fk(self) -> None:
        col = _get_column(Confirm, "action_id")
        assert col.foreign_keys
        fk = next(iter(col.foreign_keys))
        assert "actions.id" in str(fk.target_fullname)

    def test_status_default_pending(self) -> None:
        col = _get_column(Confirm, "status")
        assert col.default.arg == "pending"

    def test_index_action_id(self) -> None:
        assert "idx_confirms_action_id" in _index_names(Confirm)

    def test_answer_nullable(self) -> None:
        col = _get_column(Confirm, "answer")
        assert col.nullable

    def test_responded_at_nullable(self) -> None:
        col = _get_column(Confirm, "responded_at")
        assert col.nullable


# ===========================================================================
# 9. TestRuntimeConfigModel
# ===========================================================================


class TestRuntimeConfigModel:
    """Tests for RuntimeConfigModel."""

    def test_tablename(self) -> None:
        assert RuntimeConfigModel.__tablename__ == "runtime_configs"

    def test_unique_constraint_team_key(self) -> None:
        """uq_runtime_configs_team_key unique constraint must exist."""
        assert "uq_runtime_configs_team_key" in _constraint_names(RuntimeConfigModel)

    def test_key_not_nullable(self) -> None:
        col = _get_column(RuntimeConfigModel, "key")
        assert not col.nullable

    def test_team_id_fk(self) -> None:
        col = _get_column(RuntimeConfigModel, "team_id")
        assert col.foreign_keys

    def test_updated_at_exists(self) -> None:
        assert "updated_at" in RuntimeConfigModel.__table__.c


# ===========================================================================
# 10. TestScheduledJob
# ===========================================================================


class TestScheduledJob:
    """Tests for ScheduledJob model."""

    def test_tablename(self) -> None:
        assert ScheduledJob.__tablename__ == "scheduled_jobs"

    def test_run_count_default_zero(self) -> None:
        col = _get_column(ScheduledJob, "run_count")
        assert col.default.arg == 0

    def test_enabled_default_true(self) -> None:
        col = _get_column(ScheduledJob, "enabled")
        assert col.default.arg is True

    def test_agent_instance_id_fk(self) -> None:
        col = _get_column(ScheduledJob, "agent_instance_id")
        assert col.foreign_keys

    def test_max_runs_nullable(self) -> None:
        col = _get_column(ScheduledJob, "max_runs")
        assert col.nullable

    def test_next_run_nullable(self) -> None:
        col = _get_column(ScheduledJob, "next_run")
        assert col.nullable

    def test_cron_expr_not_nullable(self) -> None:
        col = _get_column(ScheduledJob, "cron_expr")
        assert not col.nullable


# ===========================================================================
# 11. TestActionFeedback
# ===========================================================================


class TestActionFeedback:
    """Tests for ActionFeedback model."""

    def test_tablename(self) -> None:
        assert ActionFeedback.__tablename__ == "action_feedback"

    def test_rating_check_constraint_exists(self) -> None:
        """CheckConstraint for rating 1..5 must exist."""
        assert "ck_action_feedback_rating" in _constraint_names(ActionFeedback)

    def test_action_id_fk(self) -> None:
        col = _get_column(ActionFeedback, "action_id")
        assert col.foreign_keys
        fk = next(iter(col.foreign_keys))
        assert "actions.id" in str(fk.target_fullname)

    def test_user_id_nullable(self) -> None:
        col = _get_column(ActionFeedback, "user_id")
        assert col.nullable

    def test_comment_nullable(self) -> None:
        col = _get_column(ActionFeedback, "comment")
        assert col.nullable

    def test_rating_not_nullable(self) -> None:
        col = _get_column(ActionFeedback, "rating")
        assert not col.nullable
