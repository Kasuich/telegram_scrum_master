"""
Tests for configuration module.
"""

from unittest.mock import patch

import pytest
from core.config import (
    AppConfig,
    Config,
    DatabaseConfig,
    LLMConfig,
    RuntimeConfig,
    TrackerConfig,
    YandexConfig,
    get_config,
    reload_config,
    set_config,
)
from pydantic import ValidationError


class TestDatabaseConfig:
    """Tests for DatabaseConfig."""

    def test_default_values(self) -> None:
        """Database config with all defaults."""
        config = DatabaseConfig(database_url="postgresql://localhost/test")
        assert config.database_url == "postgresql://localhost/test"
        assert config.database_pool_size == 20
        assert config.database_max_overflow == 10
        assert config.database_pool_timeout == 30

    def test_custom_values(self) -> None:
        """Database config with custom values."""
        config = DatabaseConfig(
            database_url="postgresql://user:pass@host:5432/db",
            database_pool_size=50,
            database_max_overflow=20,
        )
        assert config.database_pool_size == 50
        assert config.database_max_overflow == 20

    def test_invalid_url_scheme(self) -> None:
        """Invalid database URL scheme raises error."""
        with pytest.raises(ValidationError) as exc_info:
            DatabaseConfig(database_url="mysql://localhost/test")
        assert "database_url must start with" in str(exc_info.value)

    def test_missing_url(self) -> None:
        """Empty database URL is allowed (for services that don't use the DB)."""
        config = DatabaseConfig(database_url="")
        assert config.database_url == ""

    def test_invalid_url_rejected(self) -> None:
        """Non-empty invalid URL scheme is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            DatabaseConfig(database_url="mysql://localhost/test")
        assert "database_url must start with" in str(exc_info.value)

    def test_pool_size_bounds(self) -> None:
        """Pool size must be between 1 and 100."""
        with pytest.raises(ValidationError):
            DatabaseConfig(database_url="postgresql://x", database_pool_size=0)
        with pytest.raises(ValidationError):
            DatabaseConfig(database_url="postgresql://x", database_pool_size=101)


class TestYandexConfig:
    """Tests for YandexConfig."""

    def test_required_fields(self) -> None:
        """Both required fields must be present."""
        config = YandexConfig(
            yc_api_key="test_api_key_12345678901234567890",
            yc_folder_id="b1g1234567890abcdef",
        )
        assert config.yc_api_key == "test_api_key_12345678901234567890"
        assert config.yc_folder_id == "b1g1234567890abcdef"


class TestTrackerConfig:
    """Tests for TrackerConfig."""

    def test_default_values(self) -> None:
        """Tracker config with defaults."""
        config = TrackerConfig(
            tracker_token="test_token_123456789012345678901234567890",
            tracker_org_id="12345678901234567890",
        )
        assert config.tracker_queue == "TEST"
        assert config.tracker_api_base == "https://api.tracker.yandex.net/v3/"

    def test_custom_values(self) -> None:
        """Tracker config with custom values."""
        config = TrackerConfig(
            tracker_token="test_token_123456789012345678901234567890",
            tracker_org_id="12345678901234567890",
            tracker_queue="PROD",
        )
        assert config.tracker_queue == "PROD"

    def test_short_token_rejected(self) -> None:
        """Token shorter than 10 chars is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TrackerConfig(
                tracker_token="short",
                tracker_org_id="12345678901234567890",
            )
        assert "10 characters" in str(exc_info.value)


class TestLLMConfig:
    """Tests for LLMConfig."""

    def test_default_values(self) -> None:
        """LLM config with all defaults."""
        config = LLMConfig()
        assert config.yandexgpt_model == "gpt-oss-120b"
        assert config.yandexgpt_temperature == 0.7
        assert config.yandexgpt_max_tokens == 4000
        assert config.yandexgpt_top_p == 0.9
        assert config.yandexgpt_timeout == 60
        assert config.yandexgpt_max_retries == 3

    def test_temperature_bounds(self) -> None:
        """Temperature must be between 0 and 2."""
        config = LLMConfig(yandexgpt_temperature=0.0)
        assert config.yandexgpt_temperature == 0.0

        config = LLMConfig(yandexgpt_temperature=2.0)
        assert config.yandexgpt_temperature == 2.0

        with pytest.raises(ValidationError):
            LLMConfig(yandexgpt_temperature=-0.1)
        with pytest.raises(ValidationError):
            LLMConfig(yandexgpt_temperature=2.1)

    def test_max_tokens_bounds(self) -> None:
        """Max tokens must be positive."""
        config = LLMConfig(yandexgpt_max_tokens=1)
        assert config.yandexgpt_max_tokens == 1

        with pytest.raises(ValidationError):
            LLMConfig(yandexgpt_max_tokens=0)


class TestAppConfig:
    """Tests for AppConfig."""

    def test_default_values(self) -> None:
        """App config with defaults (DEBUG forced off to avoid .env pollution)."""
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"DEBUG": "false"}):
            config = AppConfig()
        assert config.environment == "development"
        assert config.debug is False
        assert config.log_level == "INFO"
        assert config.service_name == "pm-agent-platform"
        assert config.service_host == "0.0.0.0"
        assert config.service_port == 8000

    def test_environment_values(self) -> None:
        """Valid environment values."""
        for env in ["development", "staging", "production"]:
            config = AppConfig(environment=env)
            assert config.environment == env

    def test_invalid_environment(self) -> None:
        """Invalid environment value raises error."""
        with pytest.raises(ValidationError):
            AppConfig(environment="invalid")

    def test_log_level_values(self) -> None:
        """Valid log level values."""
        for level in ["DEBUG", "INFO", "WARNING", "ERROR"]:
            config = AppConfig(log_level=level)
            assert config.log_level == level


class TestRuntimeConfig:
    """Tests for RuntimeConfig."""

    def test_default_autonomy(self) -> None:
        """Default autonomy settings."""
        config = RuntimeConfig()
        assert config.auto_risk == ["low"]
        assert config.confirm_risk == ["medium", "high"]
        assert config.always_confirm_tools == []

    def test_team_isolation(self) -> None:
        """Team ID can be set."""
        config = RuntimeConfig(team_id="team_123")
        assert config.team_id == "team_123"

    def test_custom_autonomy(self) -> None:
        """Custom autonomy settings."""
        config = RuntimeConfig(
            auto_risk=["low"],
            confirm_risk=["medium", "high"],
            always_confirm_tools=["tracker_delete_issue"],
        )
        assert "tracker_delete_issue" in config.always_confirm_tools

    def test_feature_flags(self) -> None:
        """Feature flags can be toggled."""
        config = RuntimeConfig(
            enable_a2a=True,
            enable_alerts=False,
            enable_analytics=True,
        )
        assert config.enable_alerts is False


class TestConfig:
    """Tests for main Config class."""

    def test_nested_configs_initialized(self) -> None:
        """All nested configs are properly initialized."""
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://localhost/test",
                "YC_API_KEY": "test_key_12345678901234567890",
                "YC_FOLDER_ID": "b1g1234567890abcdef",
                "TRACKER_TOKEN": "test_token_123456789012345678901234567890",
                "TRACKER_ORG_ID": "12345678901234567890",
            },
        ):
            config = Config()

            assert isinstance(config.database, DatabaseConfig)
            assert isinstance(config.yandex, YandexConfig)
            assert isinstance(config.tracker, TrackerConfig)
            assert isinstance(config.llm, LLMConfig)
            assert isinstance(config.app, AppConfig)
            assert isinstance(config.runtime, RuntimeConfig)

    def test_database_url_shortcut(self) -> None:
        """Database URL is accessible via shortcut property."""
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://localhost/test",
                "YC_API_KEY": "test_key_12345678901234567890",
                "YC_FOLDER_ID": "b1g1234567890abcdef",
                "TRACKER_TOKEN": "test_token_123456789012345678901234567890",
                "TRACKER_ORG_ID": "12345678901234567890",
            },
        ):
            config = Config()
            assert config.database_url == "postgresql://localhost/test"

    def test_production_requires_team_id(self) -> None:
        """Production environment requires team_id."""
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://localhost/test",
                "ENVIRONMENT": "production",
                "YC_API_KEY": "test_key_12345678901234567890",
                "YC_FOLDER_ID": "b1g1234567890abcdef",
                "TRACKER_TOKEN": "test_token_123456789012345678901234567890",
                "TRACKER_ORG_ID": "12345678901234567890",
            },
        ):
            with pytest.raises(ValidationError) as exc_info:
                Config()
            assert "team_id is required in production" in str(exc_info.value)

    def test_autonomy_overlap_rejected(self) -> None:
        """Auto and confirm risk levels cannot overlap."""
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://localhost/test",
                "YC_API_KEY": "test_key_12345678901234567890",
                "YC_FOLDER_ID": "b1g1234567890abcdef",
                "TRACKER_TOKEN": "test_token_123456789012345678901234567890",
                "TRACKER_ORG_ID": "12345678901234567890",
            },
        ):
            with pytest.raises(ValidationError) as exc_info:
                Config(
                    runtime=RuntimeConfig(
                        auto_risk=["low", "medium"],
                        confirm_risk=["medium", "high"],
                    )
                )
            assert "low, medium" in str(exc_info.value).lower() or "medium" in str(exc_info.value)

    def test_for_team_method(self) -> None:
        """for_team creates config with team override."""
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://localhost/test",
                "YC_API_KEY": "test_key_12345678901234567890",
                "YC_FOLDER_ID": "b1g1234567890abcdef",
                "TRACKER_TOKEN": "test_token_123456789012345678901234567890",
                "TRACKER_ORG_ID": "12345678901234567890",
            },
        ):
            config = Config.for_team(
                team_id="team_abc",
                auto_risk=["low"],
                confirm_risk=["high"],
            )

            assert config.runtime.team_id == "team_abc"
            assert config.runtime.auto_risk == ["low"]
            assert config.runtime.confirm_risk == ["high"]


class TestGlobalConfig:
    """Tests for global config functions."""

    def test_get_config_singleton(self) -> None:
        """get_config returns singleton."""
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://localhost/test",
                "YC_API_KEY": "test_key_12345678901234567890",
                "YC_FOLDER_ID": "b1g1234567890abcdef",
                "TRACKER_TOKEN": "test_token_123456789012345678901234567890",
                "TRACKER_ORG_ID": "12345678901234567890",
            },
        ):
            set_config(None)
            config1 = get_config()
            config2 = get_config()
            assert config1 is config2

    def test_reload_config(self) -> None:
        """reload_config creates new instance."""
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://localhost/test",
                "YC_API_KEY": "test_key_12345678901234567890",
                "YC_FOLDER_ID": "b1g1234567890abcdef",
                "TRACKER_TOKEN": "test_token_123456789012345678901234567890",
                "TRACKER_ORG_ID": "12345678901234567890",
            },
        ):
            config1 = get_config()
            config2 = reload_config()
            assert config1 is not config2

    def test_set_config(self) -> None:
        """set_config updates global instance."""
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://localhost/test",
                "YC_API_KEY": "test_key_12345678901234567890",
                "YC_FOLDER_ID": "b1g1234567890abcdef",
                "TRACKER_TOKEN": "test_token_123456789012345678901234567890",
                "TRACKER_ORG_ID": "12345678901234567890",
            },
        ):
            new_config = Config()
            set_config(new_config)
            assert get_config() is new_config
