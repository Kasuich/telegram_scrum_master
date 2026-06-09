from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.config import Config, set_config
from core.tools import get_registry
from core.tracker_mcp import (
    TrackerMCPClient,
    TrackerMCPError,
    register_tracker_mcp_tools,
)


@pytest.fixture(autouse=True)
def reset_state():
    get_registry().clear()
    set_config(None)
    yield
    get_registry().clear()
    set_config(None)


@pytest.mark.asyncio
async def test_register_skips_when_mcp_is_not_configured():
    cfg = Config()
    cfg.tracker_mcp.tracker_mcp_url = ""
    cfg.tracker_mcp.tracker_mcp_token = ""
    set_config(cfg)

    assert await register_tracker_mcp_tools() == []


@pytest.mark.asyncio
async def test_register_preserves_remote_json_schema():
    cfg = Config()
    cfg.tracker_mcp.tracker_mcp_url = "https://mcp.example.test/mcp"
    cfg.tracker_mcp.tracker_mcp_token = "secret-token"
    set_config(cfg)
    definition = {
        "name": "CreateIssue",
        "description": "Create an issue",
        "inputSchema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    }

    with patch.object(
        TrackerMCPClient,
        "list_tools",
        AsyncMock(return_value=[definition]),
    ):
        assert await register_tracker_mcp_tools() == ["CreateIssue"]

    tool = get_registry().get("CreateIssue")
    assert tool.risk == "medium"
    assert tool.get_schema()["parameters"] == definition["inputSchema"]
    assert tool.validate_arguments({"summary": "MCP"}) == {"summary": "MCP"}


@pytest.mark.asyncio
async def test_call_tool_unwraps_json_text_content():
    client = TrackerMCPClient(
        url="https://mcp.example.test/mcp",
        token="secret-token",
    )
    result = {
        "content": [
            {
                "type": "text",
                "text": '{"key":"TEST-1","summary":"MCP"}',
            }
        ]
    }
    with patch.object(client, "request", AsyncMock(return_value=result)):
        assert await client.call_tool("GetIssue", {"key": "TEST-1"}) == {
            "key": "TEST-1",
            "summary": "MCP",
        }


def test_explicit_client_does_not_require_global_config():
    client = TrackerMCPClient(
        url="https://mcp.example.test/mcp",
        token="secret-token",
    )

    assert client._headers()["Authorization"] == "secret-token"


@pytest.mark.asyncio
async def test_call_tool_raises_mcp_error():
    client = TrackerMCPClient(
        url="https://mcp.example.test/mcp",
        token="secret-token",
    )
    result = {
        "isError": True,
        "content": [{"type": "text", "text": "denied"}],
    }
    with patch.object(client, "request", AsyncMock(return_value=result)):
        with pytest.raises(TrackerMCPError, match="denied"):
            await client.call_tool("DeleteGoal", {"goal_key": "G-1"})
