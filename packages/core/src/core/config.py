"""
Configuration module for PM Agent Platform.

Provides centralized configuration management with:
- Environment variable parsing (.env files)
- Pydantic v2 validation
- Team-based runtime config overrides
- Hot reload support
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig(BaseSettings):
    """Database connection configuration."""

    database_url: str = Field(
        description="PostgreSQL connection URL",
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
        """Validate database URL format."""
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

    @field_validator("tracker_token")
    @classmethod
    def validate_tracker_token(cls, v: str) -> str:
        """Validate tracker token is not empty."""
        if not v or len(v) < 10:
            raise ValueError("tracker_token must be at least 10 characters")
        return v


class LLMConfig(BaseSettings):
    """LLM (Language Model) configuration."""

    yandexgpt_model: str = Field(
        default="yandexgpt",
        description="YandexGPT model name",
        examples=["yandexgpt", "yandexgpt-lite"],
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


class AppConfig(BaseSettings):
    """Application-level configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Environment
    environment: Literal["development", "staging", "production"] = Field(
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
    llm: LLMConfig = Field(default_factory=LLMConfig)
    app: AppConfig = Field(default_factory=AppConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)

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
    "LLMConfig",
    "AppConfig",
    "RuntimeConfig",
    "get_config",
    "reload_config",
    "set_config",
]
