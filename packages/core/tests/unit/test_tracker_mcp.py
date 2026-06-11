from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from core.config import Config, set_config
from core.tools import get_registry
from core.tracker_mcp import (
    TrackerMCPClient,
    TrackerMCPError,
    _change_issue_status_via_tracker,
    _normalize_tool_arguments,
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
async def test_register_allows_public_mcp_without_gateway_token():
    cfg = Config()
    cfg.tracker_mcp.tracker_mcp_url = "https://mcp.example.test/mcp"
    cfg.tracker_mcp.tracker_mcp_token = ""
    set_config(cfg)

    with patch.object(
        TrackerMCPClient,
        "list_tools",
        AsyncMock(return_value=[{"name": "GetIssue"}]),
    ):
        assert await register_tracker_mcp_tools() == ["GetIssue"]


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


@pytest.mark.asyncio
async def test_call_tool_raises_for_error_inside_text_content():
    client = TrackerMCPClient(
        url="https://mcp.example.test/mcp",
        token="secret-token",
    )
    result = {
        "content": [
            {
                "type": "text",
                "text": '{"error":"Failed to create issue: queue not found"}',
            }
        ]
    }
    with (
        patch.object(client, "request", AsyncMock(return_value=result)),
        pytest.raises(TrackerMCPError, match="queue not found"),
    ):
        await client.call_tool("CreateIssue", {"summary": "Presentation"})


def test_create_issue_uses_configured_queue():
    cfg = Config()
    cfg.tracker.tracker_queue = "DARKHORSE"
    set_config(cfg)

    assert _normalize_tool_arguments(
        "CreateIssue",
        {"queue": "dark_horse", "summary": "Presentation"},
    ) == {
        "queue": "DARKHORSE",
        "summary": "Presentation",
    }


def test_create_issue_allows_epic_for_any_mcp_actor():
    cfg = Config()
    cfg.tracker.tracker_queue = "DARKHORSE"
    set_config(cfg)

    assert _normalize_tool_arguments(
        "CreateIssue",
        {"summary": "Epic", "issue_type": "epic"},
    ) == {
        "queue": "DARKHORSE",
        "summary": "Epic",
        "issue_type": "epic",
    }


def test_change_status_normalizes_model_resolution_alias():
    assert (
        _normalize_tool_arguments(
            "ChangeIssueStatus",
            {"issue_key": "DARKHORSE-272", "status": "closed", "resolution": "done"},
        )["resolution"]
        == "fixed"
    )


def test_change_status_adds_default_resolution_when_closing():
    assert (
        _normalize_tool_arguments(
            "ChangeIssueStatus",
            {"issue_key": "DARKHORSE-272", "status": "closed"},
        )["resolution"]
        == "fixed"
    )


@pytest.mark.asyncio
async def test_change_issue_status_uses_tracker_client_transition_resolver():
    with patch("core.tracker_mcp.TrackerClient") as mock_cls:
        client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = client
        client.transition_issue.return_value = {"status": "inProgress"}

        result = await _change_issue_status_via_tracker(
            {"issue_key": "DARKHORSE-1", "status": "inProgress"}
        )

    assert result == {"status": "inProgress"}
    client.transition_issue.assert_awaited_once_with(
        "DARKHORSE-1",
        "inProgress",
        resolution=None,
        comment=None,
    )


def test_explicit_client_does_not_require_global_config():
    client = TrackerMCPClient(
        url="https://mcp.example.test/mcp",
        token="secret-token",
    )

    assert client._headers()["Authorization"] == "secret-token"


def test_public_client_omits_authorization_header():
    client = TrackerMCPClient(
        url="https://mcp.example.test/mcp",
        token="",
    )

    assert "Authorization" not in client._headers()


@pytest.mark.asyncio
async def test_legacy_sse_gateway_lists_tools():
    requests: list[httpx.Request] = []
    events = [
        "event: endpoint",
        "data: /message?sessionId=session-1",
        "",
        "event: message",
        "data: "
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"protocolVersion": "2025-03-26"},
            }
        ),
        "",
        "event: message",
        "data: "
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"tools": [{"name": "GetIssue"}]},
            }
        ),
        "",
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                text="\n".join(events),
            )
        return httpx.Response(202)

    client = TrackerMCPClient(
        url="https://gateway.example.test/sse",
        token="",
    )
    transport = httpx.MockTransport(handler)
    with patch.object(
        client,
        "_new_client",
        return_value=httpx.AsyncClient(transport=transport, timeout=5),
    ):
        assert await client.list_tools() == [{"name": "GetIssue"}]

    assert [request.method for request in requests] == ["GET", "POST", "POST", "POST"]
    payloads = [json.loads(request.content) for request in requests[1:]]
    assert [payload["method"] for payload in payloads] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]
    assert all("authorization" not in request.headers for request in requests)


@pytest.mark.asyncio
async def test_legacy_sse_gateway_rejects_cross_origin_post_endpoint():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text="event: endpoint\ndata: https://attacker.example/message\n\n",
        )

    client = TrackerMCPClient(
        url="https://gateway.example.test/sse",
        token="secret-token",
    )
    transport = httpx.MockTransport(handler)
    with (
        patch.object(
            client,
            "_new_client",
            return_value=httpx.AsyncClient(transport=transport, timeout=5),
        ),
        pytest.raises(TrackerMCPError, match="different origin"),
    ):
        await client.list_tools()


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
