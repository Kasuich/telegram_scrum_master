"""
Test fixtures for core package tests.
"""

import os

# Minimal env so pydantic Config loads during unit tests without a real .env
os.environ.setdefault("YC_API_KEY", "test_api_key_" + "x" * 24)
os.environ.setdefault("YC_FOLDER_ID", "b1gtestfolder00000000")
os.environ.setdefault("OPENROUTER_API_KEY", "sk-or-v1-test-stub-key-0000000000")
os.environ.setdefault("TRACKER_TOKEN", "test_tracker_token_" + "x" * 16)
os.environ.setdefault("TRACKER_ORG_ID", "12345678901234567890")

import asyncio
from typing import Any, Generator
from unittest.mock import MagicMock

import pytest


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_config() -> MagicMock:
    """Mock configuration for tests."""
    config = MagicMock()
    config.database_url = "postgresql+asyncpg://test:test@localhost:5432/test"
    config.database.database_pool_size = 5
    config.database.database_max_overflow = 2
    config.database.database_pool_timeout = 10
    config.app.debug = False
    config.app.log_level = "INFO"
    config.llm.yandexgpt_model = "yandexgpt-pro"
    config.llm.yandexgpt_temperature = 0.7
    config.llm.yandexgpt_max_tokens = 4000
    config.llm.yandexgpt_timeout = 60
    config.llm.yandexgpt_max_retries = 3
    config.tracker.tracker_token = "test_token_123456789012345678901234567890"
    config.tracker.tracker_org_id = "12345678901234567890"
    config.tracker.tracker_queue = "TEST"
    config.yandex.yc_api_key = "test_api_key_12345678901234567890"
    config.yandex.yc_folder_id = "b1g1234567890abcdef"
    config.runtime.team_id = "test_team"
    config.runtime.auto_risk = ["low"]
    config.runtime.confirm_risk = ["medium", "high"]
    config.runtime.always_confirm_tools = []
    return config


@pytest.fixture
def sample_action_data() -> dict[str, Any]:
    """Sample action data for tests."""
    return {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "team_id": "550e8400-e29b-41d4-a716-446655440001",
        "agent_instance_id": "550e8400-e29b-41d4-a716-446655440002",
        "tool_name": "tracker_create_issue",
        "input": {"queue": "TEST", "summary": "Test issue"},
        "output": {"key": "TEST-1", "id": 123},
        "risk_level": "medium",
        "status": "completed",
        "trace_id": "550e8400-e29b-41d4-a716-446655440003",
    }


@pytest.fixture
def sample_trace_data() -> dict[str, Any]:
    """Sample trace data for tests."""
    return {
        "id": "550e8400-e29b-41d4-a716-446655440003",
        "session_id": "550e8400-e29b-41d4-a716-446655440004",
        "steps": [
            {"type": "thought", "content": "I need to create a task"},
            {"type": "tool_call", "tool": "tracker_create_issue", "args": {}},
            {"type": "tool_result", "content": "Task created"},
        ],
        "metadata": {"model": "yandexgpt-pro"},
    }


@pytest.fixture
def sample_tool_schema() -> dict[str, Any]:
    """Sample tool OpenAPI schema for tests."""
    return {
        "name": "test_tool",
        "description": "A test tool",
        "parameters": {
            "type": "object",
            "properties": {
                "arg1": {"type": "string", "description": "First argument"},
                "arg2": {"type": "integer", "description": "Second argument"},
            },
            "required": ["arg1"],
        },
    }
