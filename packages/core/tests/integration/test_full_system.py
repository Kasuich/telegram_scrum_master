"""
Integration tests for full system flow.

Tests components working together with mocked external services (LLM API, DB).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ENV = {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
    "YC_API_KEY": "test_api_key_12345678901234567890",
    "YC_FOLDER_ID": "b1g1234567890abcdef",
    "TRACKER_TOKEN": "test_token_123456789012345678901234567890",
    "TRACKER_ORG_ID": "12345678901234567890",
}

MOCK_LLM_RESPONSE = {
    "result": {
        "message": {"text": "Task created successfully.", "role": "assistant"},
        "usage": {"inputTokensCount": 50, "outputTokensCount": 20, "totalTokensCount": 70},
        "status": "COMPLETED",
    }
}

MOCK_TOOL_CALL_RESPONSE = {
    "result": {
        "message": {
            "functionCall": {
                "name": "create_task",
                "args": {"title": "Fix bug", "queue": "TEST"},
            }
        },
        "usage": {"inputTokensCount": 40, "outputTokensCount": 15, "totalTokensCount": 55},
        "status": "COMPLETED",
    }
}


class TestConfigDbIntegration:
    """Config and DB components working together."""

    def test_config_provides_db_url(self) -> None:
        """Config exposes DB URL that db module can consume."""
        with patch.dict("os.environ", ENV):
            from core.config import get_config, reload_config
            reload_config()
            config = get_config()

            assert config.database_url.startswith("postgresql")
            assert config.database.database_pool_size == 20

    def test_db_engine_uses_config(self) -> None:
        """DB engine is created from config URL."""
        with patch.dict("os.environ", ENV):
            from core.config import reload_config
            from core.db import create_db_engine, reset_engine

            reload_config()
            reset_engine()

            engine = create_db_engine()
            assert engine is not None
            assert "postgresql" in str(engine.url)

    def test_team_config_isolation(self) -> None:
        """Two teams get independent config instances."""
        with patch.dict("os.environ", ENV):
            from core.config import Config

            config_a = Config.for_team("team_a", auto_risk=["low"])
            config_b = Config.for_team("team_b", auto_risk=["low", "medium"])

            assert config_a.runtime.team_id == "team_a"
            assert config_b.runtime.team_id == "team_b"
            assert config_a.runtime.auto_risk != config_b.runtime.auto_risk

    def test_config_and_runtime_together(self) -> None:
        """Config autonomy settings accessible by runtime logic."""
        with patch.dict("os.environ", ENV):
            from core.config import Config

            config = Config.for_team(
                "team_test",
                auto_risk=["low"],
                confirm_risk=["medium", "high"],
                always_confirm_tools=["delete_issue"],
            )

            assert "low" in config.runtime.auto_risk
            assert "medium" in config.runtime.confirm_risk
            assert "delete_issue" in config.runtime.always_confirm_tools


class TestToolSystemIntegration:
    """Tool registry, decorator, and execution working together."""

    def setup_method(self) -> None:
        from core.tools import get_registry
        get_registry().clear()

    def test_register_and_execute_tool(self) -> None:
        """Register a tool and call it via registry."""
        from core.tools import platform_tool, get_registry

        @platform_tool(name="int_greet", risk="low", scopes=["test:read"])
        def greet(name: str, loud: bool = False) -> str:
            """Greet a user."""
            msg = f"Hello, {name}!"
            return msg.upper() if loud else msg

        registry = get_registry()
        tool = registry.get("int_greet")

        assert tool.name == "int_greet"
        assert tool.risk == "low"
        assert greet(name="Alice") == "Hello, Alice!"
        assert greet(name="Bob", loud=True) == "HELLO, BOB!"

    @pytest.mark.asyncio
    async def test_async_tool_execution(self) -> None:
        """Async tool executes correctly through registry."""
        from core.tools import platform_tool, get_registry

        @platform_tool(name="int_async_fetch", risk="medium")
        async def fetch_data(item_id: str) -> dict[str, Any]:
            """Fetch item by ID."""
            return {"id": item_id, "data": "value"}

        registry = get_registry()
        tool = registry.get("int_async_fetch")
        result = await tool.execute(item_id="abc123")

        assert result["id"] == "abc123"

    def test_tool_schema_generation(self) -> None:
        """Schema generated from tool matches OpenAPI format."""
        from core.tools import platform_tool, get_registry

        @platform_tool(name="int_schema_tool", risk="low")
        def create_item(name: str, count: int = 1) -> dict[str, Any]:
            """Create items."""
            return {"name": name, "count": count}

        schemas = get_registry().get_schemas()
        schema = next(s for s in schemas if s["name"] == "int_schema_tool")

        assert schema["description"] == "Create items."
        assert "name" in schema["parameters"]["properties"]
        assert "count" in schema["parameters"]["properties"]
        assert schema["parameters"]["properties"]["name"]["type"] == "string"
        assert schema["parameters"]["properties"]["count"]["type"] == "integer"
        assert "name" in schema["parameters"]["required"]
        assert "count" not in schema["parameters"].get("required", [])

    def test_tool_scope_filtering(self) -> None:
        """Registry scope filtering works across multiple tools."""
        from core.tools import platform_tool, get_registry

        @platform_tool(name="int_read_tool", risk="low", scopes=["tracker:read"])
        def read_op() -> str:
            return "read"

        @platform_tool(name="int_write_tool", risk="medium", scopes=["tracker:write"])
        def write_op() -> str:
            return "write"

        @platform_tool(name="int_admin_tool", risk="high", scopes=["admin"])
        def admin_op() -> str:
            return "admin"

        registry = get_registry()
        read_tools = registry.list(scopes=["tracker:read"])
        write_tools = registry.list(scopes=["tracker:write"])

        assert all(t.name == "int_read_tool" for t in read_tools)
        assert all(t.name == "int_write_tool" for t in write_tools)
        assert len(registry.list()) == 3

    def test_validation_error_propagation(self) -> None:
        """Validation errors from tools propagate cleanly."""
        from core.tools import platform_tool
        from core.exceptions import ToolValidationError

        @platform_tool(name="int_strict_tool", risk="low")
        def strict_tool(count: int) -> int:
            return count * 2

        with pytest.raises(ToolValidationError):
            strict_tool(count="not-a-number")


class TestLLMIntegration:
    """LLM client working with config and response parsing."""

    @pytest.mark.asyncio
    async def test_complete_with_text_response(self) -> None:
        """LLM returns text response, parsed into LLMResponse."""
        with patch.dict("os.environ", ENV):
            from core.config import reload_config
            reload_config()

            from core.llm import LLMClient, Message

            mock_response = MagicMock()
            mock_response.json.return_value = MOCK_LLM_RESPONSE
            mock_response.raise_for_status = MagicMock()

            with patch("core.llm.LLMClient.client") as mock_client_prop:
                mock_http = AsyncMock()
                mock_http.post = AsyncMock(return_value=mock_response)
                mock_client_prop.__get__ = MagicMock(return_value=mock_http)

                client = LLMClient()
                result = await client.complete([
                    Message(role="user", content="Create a task")
                ])

            assert result.content == "Task created successfully."
            assert result.tool_calls is None
            assert result.usage is not None
            assert result.usage.total_tokens == 70
            assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_complete_with_tool_call(self) -> None:
        """LLM returns tool call, parsed correctly."""
        with patch.dict("os.environ", ENV):
            from core.config import reload_config
            reload_config()

            from core.llm import LLMClient, Message

            mock_response = MagicMock()
            mock_response.json.return_value = MOCK_TOOL_CALL_RESPONSE
            mock_response.raise_for_status = MagicMock()

            with patch("core.llm.LLMClient.client") as mock_client_prop:
                mock_http = AsyncMock()
                mock_http.post = AsyncMock(return_value=mock_response)
                mock_client_prop.__get__ = MagicMock(return_value=mock_http)

                client = LLMClient()
                result = await client.complete([
                    Message(role="user", content="Create a task")
                ])

            assert result.content is None
            assert result.tool_calls is not None
            assert len(result.tool_calls) == 1
            assert result.tool_calls[0].name == "create_task"
            assert result.tool_calls[0].arguments["queue"] == "TEST"

    @pytest.mark.asyncio
    async def test_llm_with_tool_schemas(self) -> None:
        """LLM receives tool schemas, processes them."""
        with patch.dict("os.environ", ENV):
            from core.config import reload_config
            reload_config()

            from core.llm import LLMClient, Message
            from core.tools import platform_tool, get_registry

            get_registry().clear()

            @platform_tool(name="int_llm_create", risk="medium")
            def create_issue(summary: str, queue: str = "TEST") -> dict[str, Any]:
                """Create a Tracker issue."""
                return {"key": f"{queue}-1", "summary": summary}

            tool_schemas = get_registry().get_schemas()
            assert len(tool_schemas) == 1

            mock_response = MagicMock()
            mock_response.json.return_value = MOCK_LLM_RESPONSE
            mock_response.raise_for_status = MagicMock()

            with patch("core.llm.LLMClient.client") as mock_client_prop:
                mock_http = AsyncMock()
                mock_http.post = AsyncMock(return_value=mock_response)
                mock_client_prop.__get__ = MagicMock(return_value=mock_http)

                client = LLMClient()
                result = await client.complete(
                    [Message(role="user", content="Create a task")],
                    tools=tool_schemas,
                )

            assert result is not None
            call_kwargs = mock_http.post.call_args
            body = call_kwargs.kwargs.get("json") or call_kwargs.args[1]
            assert "generationSettings" in str(body) or "tools" in str(body)

            get_registry().clear()


class TestDbSessionIntegration:
    """DB session lifecycle integration."""

    @pytest.mark.asyncio
    async def test_session_commit_on_success(self) -> None:
        """Session commits and closes after successful block."""
        from core.db import get_session

        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()

        with patch("core.db.get_session_factory") as mock_factory:
            mock_factory.return_value = MagicMock(return_value=mock_session)

            async with get_session() as session:
                assert session is mock_session

        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()
        mock_session.rollback.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_rollback_on_error(self) -> None:
        """Session rolls back on exception, error re-raised."""
        from core.db import get_session

        mock_session = AsyncMock()

        with patch("core.db.get_session_factory") as mock_factory:
            mock_factory.return_value = MagicMock(return_value=mock_session)

            with pytest.raises(RuntimeError, match="simulated error"):
                async with get_session():
                    raise RuntimeError("simulated error")

        mock_session.rollback.assert_called_once()
        mock_session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_concurrent_sessions(self) -> None:
        """Multiple concurrent sessions handled independently."""
        from core.db import get_session

        results: list[str] = []

        async def use_session(label: str) -> None:
            mock_session = AsyncMock()
            with patch("core.db.get_session_factory") as mock_factory:
                mock_factory.return_value = MagicMock(return_value=mock_session)
                async with get_session():
                    await asyncio.sleep(0)
                    results.append(label)

        await asyncio.gather(*[use_session(f"s{i}") for i in range(10)])
        assert len(results) == 10


class TestExceptionPropagation:
    """Error handling across component boundaries."""

    def test_config_error_on_invalid_url(self) -> None:
        """Invalid DB URL raises ConfigError-like ValidationError."""
        from pydantic import ValidationError
        from core.config import DatabaseConfig

        with pytest.raises(ValidationError):
            DatabaseConfig(database_url="mysql://localhost/test")

    def test_tool_not_found_error(self) -> None:
        """ToolNotFoundError raised for unknown tool."""
        from core.tools import get_registry
        from core.exceptions import ToolNotFoundError

        get_registry().clear()
        with pytest.raises(ToolNotFoundError):
            get_registry().get("nonexistent_tool_xyz")

    def test_tool_validation_error_message(self) -> None:
        """ToolValidationError has meaningful message."""
        from core.tools import platform_tool, get_registry
        from core.exceptions import ToolValidationError

        get_registry().clear()

        @platform_tool(name="int_err_tool", risk="low")
        def needs_int(value: int) -> int:
            return value

        with pytest.raises(ToolValidationError) as exc_info:
            needs_int(value="bad-value")

        assert "value" in str(exc_info.value).lower()
        get_registry().clear()

    def test_core_error_cause_chain(self) -> None:
        """CoreError preserves cause in __cause__."""
        from core.exceptions import DBError

        original = ValueError("original error")
        wrapped = DBError("db operation failed", cause=original)

        assert wrapped.__cause__ is original
        assert wrapped.cause is original
        assert str(wrapped) == "db operation failed"


class TestLoggingIntegration:
    """Logging module working with trace IDs."""

    def test_trace_id_propagation(self) -> None:
        """Trace ID set in one place is readable elsewhere."""
        from core.logging import set_trace_id, get_trace_id

        set_trace_id("trace-abc-123")
        assert get_trace_id() == "trace-abc-123"

    def test_logger_created_with_name(self) -> None:
        """get_logger returns named logger."""
        import logging as stdlib_logging
        from core.logging import get_logger

        logger = get_logger("test.integration")
        assert logger.name == "test.integration"
        assert isinstance(logger, stdlib_logging.Logger)

    def test_timed_decorator_sync(self) -> None:
        """@timed decorator works on sync function."""
        from core.logging import timed

        @timed
        def fast_op(x: int) -> int:
            return x * 2

        result = fast_op(21)
        assert result == 42

    @pytest.mark.asyncio
    async def test_timed_decorator_async(self) -> None:
        """@timed decorator works on async function."""
        from core.logging import timed

        @timed
        async def async_op(x: int) -> int:
            return x * 3

        result = await async_op(14)
        assert result == 42


class TestPromptsIntegration:
    """Prompt templates integration."""

    def test_confirm_prompt_contains_tool_name(self) -> None:
        """Confirm prompt includes the tool name and args."""
        from core.prompts import format_confirm_prompt

        prompt = format_confirm_prompt(
            tool_name="tracker_create_issue",
            arguments={"queue": "TEST", "summary": "Fix bug"},
            risk_level="medium",
        )

        assert "tracker_create_issue" in prompt
        assert "medium" in prompt.lower() or "средний" in prompt.lower()

    def test_tool_descriptions_format(self) -> None:
        """Tool descriptions render all tools."""
        from core.prompts import format_tool_descriptions

        tools = [
            {
                "name": "create_issue",
                "description": "Create a tracker issue",
                "parameters": {"properties": {"summary": {"type": "string"}}},
                "risk": "medium",
            },
            {
                "name": "list_issues",
                "description": "List tracker issues",
                "parameters": {"properties": {}},
                "risk": "low",
            },
        ]

        result = format_tool_descriptions(tools)
        assert "create_issue" in result
        assert "list_issues" in result

    def test_error_message_format(self) -> None:
        """Error message includes type and message."""
        from core.prompts import format_error_message

        msg = format_error_message(
            error_type="ToolNotFoundError",
            message="Tool 'xyz' is not registered",
            context={"available_tools": ["a", "b"]},
        )

        assert "ToolNotFoundError" in msg
        assert "xyz" in msg

    def test_system_prompt_is_nonempty(self) -> None:
        """System prompt is a non-empty string."""
        from core.prompts import PM_AGENT_SYSTEM_PROMPT

        assert isinstance(PM_AGENT_SYSTEM_PROMPT, str)
        assert len(PM_AGENT_SYSTEM_PROMPT) > 100
