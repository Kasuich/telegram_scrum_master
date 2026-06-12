"""
Configuration module for PM Agent Platform.

Provides centralized configuration management with:
- Environment variable parsing (.env files)
- Pydantic v2 validation
- Team-based runtime config overrides
- Hot reload support
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig(BaseSettings):
    """Database connection configuration."""

    database_url: str = Field(
        default="",
        description="PostgreSQL connection URL (optional for services that don't use the DB)",
        examples=["postgresql+asyncpg://user:pass@localhost:5432/pm_agent"],
    )

    database_pool_size: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Connection pool size",
    )

    database_max_overflow: int = Field(
        default=10,
        ge=0,
        le=50,
        description="Max overflow connections",
    )

    database_pool_timeout: int = Field(
        default=30,
        ge=1,
        description="Pool acquire timeout in seconds",
    )

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """Validate database URL format (skip if empty — service doesn't use DB)."""
        if not v:
            return v
        if not v.startswith(("postgresql+asyncpg://", "postgresql://", "sqlite://")):
            raise ValueError(
                "database_url must start with 'postgresql+asyncpg://', "
                "'postgresql://', or 'sqlite://'"
            )
        return v


class YandexConfig(BaseSettings):
    """Yandex Cloud configuration."""

    yc_api_key: str = Field(
        description="Yandex Cloud API key for service account",
    )

    yc_folder_id: str = Field(
        description="Yandex Cloud folder ID",
        examples=["b1g******"],  # noqa: RUF001
    )


class TrackerConfig(BaseSettings):
    """Yandex Tracker configuration."""

    tracker_token: str = Field(
        description="OAuth token for Yandex Tracker API",
    )

    tracker_org_id: str = Field(
        description="Organization ID for Yandex Tracker",
    )

    tracker_org_type: str = Field(
        default="360",
        description="Organization type: '360' (X-Org-ID) or 'cloud' (X-Cloud-Org-ID)",
    )

    tracker_queue: str = Field(
        default="TEST",
        description="Default queue key",
        examples=["TEST", "DARKHORSE", "BACKEND"],
    )

    tracker_api_base: str = Field(
        default="https://api.tracker.yandex.net/v3/",
        description="Yandex Tracker API base URL",
    )

    tracker_dedup_enabled: bool = Field(
        default=True,
        description="Check for duplicate issues before tracker_create_issue",
    )

    @field_validator("tracker_token")
    @classmethod
    def validate_tracker_token(cls, v: str) -> str:
        """Validate non-empty tracker tokens while allowing services to boot without Tracker."""
        if not v:
            return v
        if len(v) < 10:
            raise ValueError("tracker_token must be at least 10 characters")
        return v


class TrackerMCPConfig(BaseSettings):
    """Yandex Tracker MCP gateway configuration."""

    tracker_mcp_url: str = Field(
        default="",
        description="Streamable HTTP or HTTP+SSE endpoint of the Tracker MCP server",
    )
    tracker_mcp_token: str = Field(
        default="",
        description="Access token sent in the Tracker MCP Authorization header",
    )
    tracker_mcp_timeout: float = Field(
        default=60.0,
        gt=0,
        description="Tracker MCP request timeout in seconds",
    )


class LLMConfig(BaseSettings):
    """LLM (Language Model) configuration."""

    yandexgpt_model: str = Field(
        default="gpt-oss-120b",
        description="Default model name (served via Yandex OpenAI-compatible Responses API)",
        examples=["yandexgpt", "yandexgpt-lite", "gpt-oss-120b", "gpt-oss-20b"],
    )

    yandexgpt_temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )

    yandexgpt_max_tokens: int = Field(
        default=4000,
        ge=1,
        le=16000,
        description="Maximum tokens in response",
    )

    yandexgpt_top_p: float = Field(
        default=0.9,
        ge=0.0,
        le=1.0,
        description="Nucleus sampling threshold",
    )

    yandexgpt_timeout: int = Field(
        default=60,
        ge=1,
        description="Request timeout in seconds",
    )

    yandexgpt_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum retry attempts",
    )

    openrouter_api_key: str = Field(
        default="",
        description="OpenRouter API key for third-party models (Gemini, Claude, etc.)",
    )

    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="OpenRouter API base URL",
    )

    openrouter_default_model: str = Field(
        default="google/gemini-3.1-flash-lite",
        description="Default model for OpenRouter requests",
    )


class AppConfig(BaseSettings):
    """Application-level configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Environment
    environment: Literal["development", "test", "staging", "production"] = Field(
        default="development",
        description="Runtime environment",
    )

    debug: bool = Field(
        default=False,
        description="Enable debug mode",
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Logging level",
    )

    # Service
    service_name: str = Field(
        default="pm-agent-platform",
        description="Service identifier",
    )

    service_host: str = Field(
        default="0.0.0.0",
        description="Service bind host",
    )

    service_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="Service bind port",
    )

    # Default team for single-tenant deployments. When set together with a
    # database_url, the orchestrator persists actions/traces/confirms under
    # this team. Read from env var DEFAULT_TEAM_ID.
    default_team_id: str | None = Field(
        default=None,
        description="Default team UUID for DB persistence (single-tenant)",
    )

    # Enable the scheduler daemon (set to false in tests/dev to avoid asyncio loops).
    scheduler_enabled: bool = Field(
        default=True,
        description="Enable the background scheduler daemon",
    )


class BacklogConfig(BaseSettings):
    """Backlog planning from meeting summaries."""

    model_config = SettingsConfigDict(
        env_prefix="backlog_",
        extra="ignore",
    )

    velocity_sp_per_week: float = Field(
        default=20.0,
        ge=1.0,
        description="Team velocity (story points per week) for deadline estimation",
    )

    start_date: str = Field(
        default="",
        description="Optional plan start date YYYY-MM-DD (default: today)",
    )

    min_summary_chars: int = Field(
        default=800,
        ge=200,
        description="Auto-detect backlog intent when message length exceeds this",
    )

    dedup_enabled: bool = Field(
        default=True,
        description="Skip creating backlog issues that already exist in the queue",
    )

    dedup_similarity: float = Field(
        default=0.65,
        ge=0.5,
        le=1.0,
        description="Summary similarity threshold for duplicate detection (0-1)",
    )

    def start_date_parsed(self) -> date:
        if self.start_date.strip():
            return date.fromisoformat(self.start_date.strip())
        return date.today()


class DailyDigestConfig(BaseSettings):
    """Daily Tracker digest sent through Telegram outbox."""

    model_config = SettingsConfigDict(
        env_prefix="daily_digest_",
        extra="ignore",
    )

    enabled: bool = Field(
        default=True,
        description="Enable the team daily Telegram digest scheduled job",
    )

    cron_expr: str = Field(
        default="0 * * * *",
        description="UTC cron expression for the digest",
    )

    timezone: str = Field(
        default="Europe/Moscow",
        description="Timezone used for the digest day window",
    )

    telegram_chat_id: str = Field(
        default="",
        description="Optional target Telegram chat id for the digest",
    )

    in_progress_statuses: str = Field(
        default="In Progress,\u0412 \u0440\u0430\u0431\u043e\u0442\u0435",
        description="Comma-separated Tracker statuses treated as in-progress",
    )

    max_issues_per_section: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum issues shown per member section",
    )

    def in_progress_status_list(self) -> list[str]:
        return [
            part.strip()
            for part in self.in_progress_statuses.replace(";", ",").split(",")
            if part.strip()
        ]


class StandupPollConfig(BaseSettings):
    """Hourly Telegram standup poll sent before the team digest."""

    model_config = SettingsConfigDict(
        env_prefix="standup_poll_",
        extra="ignore",
    )

    enabled: bool = Field(
        default=True,
        description="Enable private standup poll messages before each digest",
    )

    cron_expr: str = Field(
        default="50 * * * *",
        description="UTC cron expression for the private standup poll",
    )

    lead_minutes: int = Field(
        default=10,
        ge=0,
        le=59,
        description="Minutes before the digest slot represented by a poll",
    )

    max_issues_per_member: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum open issues shown in one private poll",
    )

    blocked_transition_aliases: str = Field(
        default="blocked,\u0411\u043b\u043e\u043a\u0435\u0440,"
        "\u0417\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u043d\u043e,"
        "\u0417\u0430\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u0442\u0441\u044f",
        description="Comma-separated transition aliases used for blocked/delayed tasks",
    )

    def blocked_transition_alias_list(self) -> list[str]:
        return [
            part.strip()
            for part in self.blocked_transition_aliases.replace(";", ",").split(",")
            if part.strip()
        ]


class DeadlineReminderConfig(BaseSettings):
    """Per-assignee deadline reminder DMs + lead summary via Telegram outbox."""

    model_config = SettingsConfigDict(
        env_prefix="deadline_reminder_",
        extra="ignore",
    )

    enabled: bool = Field(
        default=True,
        description="Enable the deadline reminder scheduled job",
    )

    cron_expr: str = Field(
        default="0 * * * *",
        description=(
            "UTC cron expression. Phase 1 (hourly): '0 * * * *'; "
            "Phase 2 (daily 16:00 MSK): '0 13 * * *'"
        ),
    )

    timezone: str = Field(
        default="Europe/Moscow",
        description="Timezone for 'today' reference and day window",
    )

    soon_days: int = Field(
        default=3,
        ge=0,
        le=30,
        description="Days ahead counted as 'due soon'",
    )

    max_issues_per_member: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Cap on issues included per per-assignee DM",
    )

    notify_assignees: bool = Field(
        default=True,
        description="Send per-assignee private DMs",
    )

    notify_lead: bool = Field(
        default=True,
        description="Send consolidated lead summary DM",
    )

    lead_roles: str = Field(
        default="lead,admin",
        description=(
            "Comma-separated team_memberships.role values that receive the lead summary"
        ),
    )

    lead_login: str = Field(
        default="nukolaus",
        description=(
            "Fallback tracker_login for lead summary when no member holds a lead role"
        ),
    )

    def lead_role_list(self) -> list[str]:
        return [
            part.strip()
            for part in self.lead_roles.replace(";", ",").split(",")
            if part.strip()
        ]


class RuntimeConfig(BaseSettings):
    """Runtime configuration that can be overridden per team."""

    model_config = SettingsConfigDict(
        env_prefix="runtime_",
        extra="ignore",
    )

    # Team ID for multi-tenancy (set at runtime)
    team_id: str | None = Field(
        default=None,
        description="Current team ID for tenant isolation",
    )

    # Autonomy settings
    auto_risk: list[Literal["low", "medium", "high"]] = Field(
        default=["low"],
        description="Risk levels that execute automatically",
    )

    confirm_risk: list[Literal["low", "medium", "high"]] = Field(
        default=["medium", "high"],
        description="Risk levels requiring confirmation",
    )

    always_confirm_tools: list[str] = Field(
        default_factory=list,
        description="Tools that always require confirmation regardless of risk",
    )

    skip_tool_confirm: bool = Field(
        default=False,
        description="Run all tools immediately (no pending_confirm / resume step)",
    )

    # Per-tool overrides (set via agent overlay, see effective_config)
    disabled_tools: list[str] = Field(
        default_factory=list,
        description="Tool names disabled for this agent (excluded from the LLM tool set)",
    )

    tool_confirm: dict[str, bool] = Field(
        default_factory=dict,
        description="Per-tool confirmation override: True=always confirm, False=auto-run",
    )

    # Feature flags
    enable_a2a: bool = Field(
        default=True,
        description="Enable agent-to-agent communication",
    )

    enable_alerts: bool = Field(
        default=True,
        description="Enable alert notifications",
    )

    enable_analytics: bool = Field(
        default=True,
        description="Enable analytics tracking",
    )


class Config(BaseSettings):
    """
    Main configuration class combining all config sections.

    Load order (later overrides earlier):
    1. Default values
    2. .env file
    3. Environment variables
    4. Runtime team overlay (programmatic)
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Nested configs
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    yandex: YandexConfig = Field(default_factory=YandexConfig)
    tracker: TrackerConfig = Field(default_factory=TrackerConfig)
    tracker_mcp: TrackerMCPConfig = Field(default_factory=TrackerMCPConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    app: AppConfig = Field(default_factory=AppConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    backlog: BacklogConfig = Field(default_factory=BacklogConfig)
    daily_digest: DailyDigestConfig = Field(default_factory=DailyDigestConfig)
    standup_poll: StandupPollConfig = Field(default_factory=StandupPollConfig)
    deadline_reminder: DeadlineReminderConfig = Field(default_factory=DeadlineReminderConfig)

    allow_real_tracker_eval: bool = Field(
        default=False,
        description="Allow eval runs with use_real_tracker=true",
    )

    # Database URL shortcut (delegates to database.database_url)
    @property
    def database_url(self) -> str:
        """Get database URL from nested config."""
        return self.database.database_url

    @model_validator(mode="after")
    def validate_config(self) -> Config:
        """Validate configuration consistency."""
        # Validate team_id is set for production
        if self.app.environment == "production" and not self.runtime.team_id:
            raise ValueError("team_id is required in production environment")

        # Validate autonomy config is consistent
        if self.runtime.auto_risk and self.runtime.confirm_risk:
            overlap = set(self.runtime.auto_risk) & set(self.runtime.confirm_risk)
            if overlap:
                raise ValueError(
                    f"Risk levels {overlap} cannot be in both auto_risk and confirm_risk"
                )

        return self

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> Config:
        """
        Load configuration from environment.

        Args:
            env_file: Optional path to .env file

        Returns:
            Config instance
        """
        if env_file is None:
            return cls()

        env_path = Path(env_file)
        if not env_path.exists():
            import warnings

            warnings.warn(f"Env file not found: {env_file}, using defaults")
            return cls()

        # Create a dynamic subclass so we don't mutate the shared class-level model_config
        DynamicConfig = type(
            "DynamicConfig",
            (cls,),
            {"model_config": SettingsConfigDict(**{**cls.model_config, "env_file": str(env_path)})},
        )
        return DynamicConfig()

    @classmethod
    def for_team(cls, team_id: str, **overrides: Any) -> Config:
        """
        Load configuration for a specific team with overrides.

        Args:
            team_id: Team identifier for tenant isolation
            **overrides: Runtime config overrides

        Returns:
            Config instance configured for team
        """
        config = cls()
        config.runtime.team_id = team_id

        # Apply overrides
        if "auto_risk" in overrides:
            config.runtime.auto_risk = overrides["auto_risk"]
        if "confirm_risk" in overrides:
            config.runtime.confirm_risk = overrides["confirm_risk"]
        if "always_confirm_tools" in overrides:
            config.runtime.always_confirm_tools = overrides["always_confirm_tools"]

        return config


# Global config instance (lazy loaded)
_config: Config | None = None


def get_config() -> Config:
    """
    Get the global configuration instance.

    Returns:
        Config singleton
    """
    global _config
    if _config is None:
        _config = Config()
    return _config


def reload_config() -> Config:
    """
    Reload configuration from environment.

    Returns:
        New Config instance
    """
    global _config
    _config = Config()
    return _config


def set_config(config: Config | None) -> None:
    """
    Set the global configuration instance.

    Args:
        config: Config instance to use, or None to clear the singleton
    """
    global _config
    _config = config


# Re-export commonly used types
__all__ = [
    "Config",
    "DatabaseConfig",
    "YandexConfig",
    "TrackerConfig",
    "TrackerMCPConfig",
    "LLMConfig",
    "AppConfig",
    "RuntimeConfig",
    "DailyDigestConfig",
    "DeadlineReminderConfig",
    "StandupPollConfig",
    "get_config",
    "reload_config",
    "set_config",
]
