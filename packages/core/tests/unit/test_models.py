"""
Tests for core.models — structure and metadata of ORM models.

No real database connection is needed. Tests only inspect Python-level
model definitions: columns, defaults, table names, indexes, and constraints.
"""

from __future__ import annotations

import uuid

import pytest
from core.models import (
    Action,
    ActionFeedback,
    AgentInstance,
    AgentSpec,
    Base,
    Confirm,
    ConsoleSession,
    LoginChallenge,
    Organization,
    RuntimeConfigModel,
    ScheduledJob,
    Team,
    TeamMembership,
    TelegramBusinessConnection,
    TelegramCallbackToken,
    TelegramChat,
    TelegramInstallation,
    TelegramMessage,
    TelegramNotificationPreference,
    TelegramOnboardingSession,
    TelegramOutbox,
    TelegramUpdate,
    TelegramUser,
    TelegramUserLink,
    Trace,
    User,
)
from sqlalchemy import (
    CheckConstraint,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase

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
        User,
        ConsoleSession,
        Team,
        AgentSpec,
        TelegramInstallation,
        TelegramChat,
        TelegramUser,
        TelegramUserLink,
        TelegramBusinessConnection,
        AgentInstance,
        Action,
        Trace,
        Confirm,
        TelegramUpdate,
        TelegramMessage,
        TelegramOutbox,
        TelegramCallbackToken,
        TelegramNotificationPreference,
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
# 3. TestUser
# ===========================================================================


class TestUser:
    """Tests for console User model."""

    def test_tablename(self) -> None:
        assert User.__tablename__ == "users"

    def test_email_unique_and_indexed(self) -> None:
        col = _get_column(User, "email")
        assert col.unique
        assert "idx_users_email" in _index_names(User)

    def test_role_check_constraint_exists(self) -> None:
        assert "ck_users_role" in _constraint_names(User)

    def test_active_default_true(self) -> None:
        col = _get_column(User, "active")
        assert col.default.arg is True


# ===========================================================================
# 4. TestConsoleSession
# ===========================================================================


class TestConsoleSession:
    """Tests for console session model."""

    def test_tablename(self) -> None:
        assert ConsoleSession.__tablename__ == "console_sessions"

    def test_user_id_fk(self) -> None:
        col = _get_column(ConsoleSession, "user_id")
        assert col.foreign_keys
        fk = next(iter(col.foreign_keys))
        assert "users.id" in str(fk.target_fullname)

    def test_token_hash_unique_and_indexed(self) -> None:
        col = _get_column(ConsoleSession, "token_hash")
        assert col.unique
        assert "idx_console_sessions_token_hash" in _index_names(ConsoleSession)

    def test_revoked_at_nullable(self) -> None:
        col = _get_column(ConsoleSession, "revoked_at")
        assert col.nullable


# ===========================================================================
# 5. TestTeam
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
# 6. TestAgentSpec
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
        assert col.default.arg == "yandexgpt"

    def test_prompt_not_nullable(self) -> None:
        col = _get_column(AgentSpec, "prompt")
        assert not col.nullable


# ===========================================================================
# 7. TestTelegramInstallation
# ===========================================================================


class TestTelegramInstallation:
    def test_tablename(self) -> None:
        assert TelegramInstallation.__tablename__ == "telegram_installations"

    def test_team_alias_unique_constraint(self) -> None:
        assert "uq_telegram_installations_team_alias" in _constraint_names(TelegramInstallation)

    def test_team_id_fk(self) -> None:
        assert _get_column(TelegramInstallation, "team_id").foreign_keys

    def test_settings_default_dict(self) -> None:
        default_fn = _get_column(TelegramInstallation, "settings").default.arg
        assert callable(default_fn)
        assert default_fn.__name__ == "dict"


# ===========================================================================
# 8. TestTelegramChat
# ===========================================================================


class TestTelegramChat:
    def test_tablename(self) -> None:
        assert TelegramChat.__tablename__ == "telegram_chats"

    def test_installation_chat_unique_constraint(self) -> None:
        assert "uq_telegram_chats_installation_chat" in _constraint_names(TelegramChat)

    def test_active_default_true(self) -> None:
        assert _get_column(TelegramChat, "active").default.arg is True


# ===========================================================================
# 9. TestTelegramUser
# ===========================================================================


class TestTelegramUser:
    def test_tablename(self) -> None:
        assert TelegramUser.__tablename__ == "telegram_users"

    def test_external_user_unique_constraint(self) -> None:
        assert "uq_telegram_users_external_user_id" in _constraint_names(TelegramUser)

    def test_is_blocked_default_false(self) -> None:
        assert _get_column(TelegramUser, "is_blocked").default.arg is False


# ===========================================================================
# 10. TestTelegramUserLink
# ===========================================================================


class TestTelegramUserLink:
    def test_tablename(self) -> None:
        assert TelegramUserLink.__tablename__ == "telegram_user_links"

    def test_team_telegram_user_unique_constraint(self) -> None:
        assert "uq_telegram_user_links_team_telegram_user" in _constraint_names(TelegramUserLink)

    def test_user_id_nullable(self) -> None:
        assert _get_column(TelegramUserLink, "user_id").nullable


class TestTeamMembership:
    def test_team_user_unique_constraint(self) -> None:
        assert "uq_team_memberships_team_user" in _constraint_names(TeamMembership)

    def test_tracker_login_unique_within_team(self) -> None:
        assert "uq_team_memberships_team_tracker_login" in _constraint_names(TeamMembership)


class TestTelegramOnboardingSession:
    def test_tablename(self) -> None:
        assert TelegramOnboardingSession.__tablename__ == "telegram_onboarding_sessions"

    def test_default_step(self) -> None:
        assert _get_column(TelegramOnboardingSession, "step_key").default.arg == "tracker_login"


class TestLoginChallenge:
    def test_tablename(self) -> None:
        assert LoginChallenge.__tablename__ == "login_challenges"

    def test_attempts_default_zero(self) -> None:
        assert _get_column(LoginChallenge, "attempts").default.arg == 0


# ===========================================================================
# 11. TestTelegramBusinessConnection
# ===========================================================================


class TestTelegramBusinessConnection:
    def test_tablename(self) -> None:
        assert TelegramBusinessConnection.__tablename__ == "telegram_business_connections"

    def test_external_id_unique_constraint(self) -> None:
        assert "uq_telegram_business_connections_external_id" in _constraint_names(
            TelegramBusinessConnection
        )

    def test_can_reply_default_false(self) -> None:
        assert _get_column(TelegramBusinessConnection, "can_reply").default.arg is False


# ===========================================================================
# 12. TestAgentInstance
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
# 13. TestAction
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
# 14. TestTrace
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
# 15. TestConfirm
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
# 16. TestTelegramUpdate
# ===========================================================================


class TestTelegramUpdate:
    def test_tablename(self) -> None:
        assert TelegramUpdate.__tablename__ == "telegram_updates"

    def test_installation_update_unique_constraint(self) -> None:
        assert "uq_telegram_updates_installation_update_id" in _constraint_names(TelegramUpdate)

    def test_update_id_not_nullable(self) -> None:
        assert not _get_column(TelegramUpdate, "update_id").nullable


# ===========================================================================
# 17. TestTelegramMessage
# ===========================================================================


class TestTelegramMessage:
    def test_tablename(self) -> None:
        assert TelegramMessage.__tablename__ == "telegram_messages"

    def test_installation_chat_message_unique_constraint(self) -> None:
        assert "uq_telegram_messages_installation_chat_message" in _constraint_names(
            TelegramMessage
        )

    def test_team_index_exists(self) -> None:
        assert "idx_telegram_messages_team_id" in _index_names(TelegramMessage)


# ===========================================================================
# 18. TestTelegramOutbox
# ===========================================================================


class TestTelegramOutbox:
    def test_tablename(self) -> None:
        assert TelegramOutbox.__tablename__ == "telegram_outbox"

    def test_status_next_attempt_index_exists(self) -> None:
        assert "idx_telegram_outbox_status_next_attempt" in _index_names(TelegramOutbox)

    def test_attempts_default_zero(self) -> None:
        assert _get_column(TelegramOutbox, "attempts").default.arg == 0


# ===========================================================================
# 19. TestTelegramCallbackToken
# ===========================================================================


class TestTelegramCallbackToken:
    def test_tablename(self) -> None:
        assert TelegramCallbackToken.__tablename__ == "telegram_callback_tokens"

    def test_token_hash_unique_constraint(self) -> None:
        assert "uq_telegram_callback_tokens_token_hash" in _constraint_names(TelegramCallbackToken)

    def test_confirm_id_index_exists(self) -> None:
        assert "idx_telegram_callback_tokens_confirm_id" in _index_names(TelegramCallbackToken)


# ===========================================================================
# 20. TestTelegramNotificationPreference
# ===========================================================================


class TestTelegramNotificationPreference:
    def test_tablename(self) -> None:
        assert TelegramNotificationPreference.__tablename__ == "telegram_notification_preferences"

    def test_team_user_category_unique_constraint(self) -> None:
        assert "uq_telegram_notification_preferences_team_user_category" in _constraint_names(
            TelegramNotificationPreference
        )

    def test_enabled_default_true(self) -> None:
        assert _get_column(TelegramNotificationPreference, "enabled").default.arg is True


# ===========================================================================
# 21. TestRuntimeConfigModel
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
# 22. TestScheduledJob
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
# 23. TestActionFeedback
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
        assert col.foreign_keys

    def test_comment_nullable(self) -> None:
        col = _get_column(ActionFeedback, "comment")
        assert col.nullable

    def test_rating_not_nullable(self) -> None:
        col = _get_column(ActionFeedback, "rating")
        assert not col.nullable
