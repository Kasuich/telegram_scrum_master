"""
Example 01: Configuration management.

Shows how to load config, use team overlays, and validate settings.
Run: python -m examples.01_config_usage  (from packages/core/)
"""

from __future__ import annotations

import os

# Set minimal env vars for the example to run without a real .env
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/pm_agent")
os.environ.setdefault("YC_API_KEY", "example_api_key_12345678901234567890")
os.environ.setdefault("YC_FOLDER_ID", "b1g1234567890abcdef")
os.environ.setdefault("TRACKER_TOKEN", "example_oauth_token_12345678901234567890")
os.environ.setdefault("TRACKER_ORG_ID", "12345678901234567890")


def main() -> None:
    from core.config import Config, get_config, reload_config

    # --- Basic config loading ---
    config = get_config()
    print(f"Environment: {config.app.environment}")
    print(f"DB pool size: {config.database.database_pool_size}")
    print(f"LLM model: {config.llm.yandexgpt_model}")
    print(f"LLM temperature: {config.llm.yandexgpt_temperature}")

    # --- Team overlay ---
    team_config = Config.for_team(
        team_id="team_backend",
        auto_risk=["low"],
        confirm_risk=["medium", "high"],
        always_confirm_tools=["tracker_delete_issue"],
    )
    print(f"\nTeam: {team_config.runtime.team_id}")
    print(f"Auto risk levels: {team_config.runtime.auto_risk}")
    print(f"Always confirm: {team_config.runtime.always_confirm_tools}")

    # --- Two teams with different settings ---
    team_a = Config.for_team("team_a", auto_risk=["low"])
    team_b = Config.for_team("team_b", auto_risk=["low", "medium"])

    print(f"\nTeam A auto_risk: {team_a.runtime.auto_risk}")
    print(f"Team B auto_risk: {team_b.runtime.auto_risk}")
    assert team_a.runtime.team_id != team_b.runtime.team_id

    # --- Reload config after env change ---
    os.environ["LOG_LEVEL"] = "DEBUG"
    new_config = reload_config()
    print(f"\nReloaded log level: {new_config.app.log_level}")


if __name__ == "__main__":
    main()
