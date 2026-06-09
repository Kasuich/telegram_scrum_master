"""Client and ToolRegistry adapter for the Yandex Tracker MCP server."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from core.config import get_config
from core.exceptions import CoreError
from core.tools import Tool, get_registry

logger = logging.getLogger(__name__)


class TrackerMCPError(CoreError):
    """Tracker MCP transport or protocol error."""


_RISK_BY_TOOL: dict[str, str] = {
    "GetIssue": "low",
    "GetIssueLinks": "low",
    "GetIssues": "low",
    "GetProject": "low",
    "GetPortfolio": "low",
    "GetGoal": "low",
    "SearchEntities": "low",
    "CreateComment": "low",
    "CreateIssue": "medium",
    "UpdateIssue": "medium",
    "ChangeIssueStatus": "high",
    "BulkUpdate": "medium",
    "BulkTransition": "high",
    "BulkMove": "high",
    "WaitForBulkChange": "low",
    "CreateGoal": "medium",
    "UpdateGoal": "medium",
    "DeleteGoal": "high",
    "BulkUpdateMetaEntities": "medium",
}

_READ_TOOLS = {
    "GetIssue",
    "GetIssueLinks",
    "GetIssues",
    "GetProject",
    "GetPortfolio",
    "GetGoal",
    "SearchEntities",
    "WaitForBulkChange",
}


class TrackerMCPClient:
    """Minimal stateless Streamable HTTP MCP client."""

    def __init__(
        self,
        *,
        url: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
    ) -> None:
        if url is not None and token is not None:
            timeout = timeout if timeout is not None else 60.0
        else:
            cfg = get_config().tracker_mcp
            url = url if url is not None else cfg.tracker_mcp_url
            token = token if token is not None else cfg.tracker_mcp_token
            timeout = timeout if timeout is not None else cfg.tracker_mcp_timeout
        self._url = url.strip()
        self._token = token
        self._timeout = timeout
        self._request_id = 0

    def _headers(self) -> dict[str, str]:
        if not self._url:
            raise TrackerMCPError("TRACKER_MCP_URL is not configured")
        if not self._token:
            raise TrackerMCPError("TRACKER_MCP_TOKEN is not configured")
        return {
            "Authorization": self._token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                self._url,
                headers=self._headers(),
                json=payload,
            )
        if response.status_code >= 400:
            raise TrackerMCPError(
                f"Tracker MCP HTTP {response.status_code}: {response.text[:300]}"
            )
        data = response.json()
        if data.get("error"):
            error = data["error"]
            raise TrackerMCPError(
                f"Tracker MCP {error.get('code')}: {error.get('message')}"
            )
        return data.get("result")

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self.request("tools/list")
        return list((result or {}).get("tools") or [])

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = await self.request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        if not isinstance(result, dict):
            return result
        if result.get("isError"):
            raise TrackerMCPError(_content_text(result) or f"{name} failed")
        content = result.get("content")
        if not isinstance(content, list):
            return result
        texts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if len(texts) != 1:
            return result
        try:
            return json.loads(texts[0])
        except (TypeError, json.JSONDecodeError):
            return texts[0]


def _content_text(result: dict[str, Any]) -> str:
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


async def register_tracker_mcp_tools() -> list[str]:
    """Discover remote Tracker tools and register local proxy Tool objects."""
    cfg = get_config().tracker_mcp
    if not cfg.tracker_mcp_url or not cfg.tracker_mcp_token:
        logger.warning(
            "Tracker MCP tools are disabled: set TRACKER_MCP_URL and TRACKER_MCP_TOKEN"
        )
        return []
    client = TrackerMCPClient()
    definitions = await client.list_tools()
    registry = get_registry()
    registered: list[str] = []

    for definition in definitions:
        name = str(definition.get("name", "")).strip()
        if not name:
            continue

        async def invoke(_tool_name: str = name, **kwargs: Any) -> Any:
            return await TrackerMCPClient().call_tool(_tool_name, kwargs)

        if registry.exists(name):
            registry.unregister(name)
        registry.register(
            Tool(
                name=name,
                description=str(definition.get("description", "")),
                func=invoke,
                risk=_RISK_BY_TOOL.get(name, "medium"),
                scopes=["tracker:read" if name in _READ_TOOLS else "tracker:write"],
                input_schema=definition.get("inputSchema") or {
                    "type": "object",
                    "properties": {},
                },
                passthrough_arguments=True,
            )
        )
        registered.append(name)
    return registered


__all__ = [
    "TrackerMCPClient",
    "TrackerMCPError",
    "register_tracker_mcp_tools",
]
