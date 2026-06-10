"""Client and ToolRegistry adapter for the Yandex Tracker MCP server."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urljoin, urlsplit

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
    """Minimal MCP client supporting Streamable HTTP and legacy HTTP+SSE."""

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
        self._session_id: str | None = None

    def _headers(
        self,
        *,
        content_type: bool = True,
        accept: str = "application/json, text/event-stream",
    ) -> dict[str, str]:
        if not self._url:
            raise TrackerMCPError("TRACKER_MCP_URL is not configured")

        headers = {"Accept": accept}
        if content_type:
            headers["Content-Type"] = "application/json"
        # Only send Authorization when a gateway token is configured. The public
        # Yandex serverless-container ingress validates `Authorization` as an IAM
        # token and rejects unknown values, so an empty token must stay unset.
        if self._token:
            headers["Authorization"] = self._token
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout)

    def _uses_legacy_sse(self) -> bool:
        return urlsplit(self._url).path.rstrip("/").endswith("/sse")

    @staticmethod
    def _parse_response(response: httpx.Response) -> Any:
        """Streamable HTTP replies with JSON or an SSE-framed `data:` line."""
        if "text/event-stream" in response.headers.get("content-type", ""):
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    chunk = line[5:].strip()
                    try:
                        return json.loads(chunk)
                    except (TypeError, json.JSONDecodeError):
                        return None
            return None
        try:
            return response.json()
        except json.JSONDecodeError:
            return None

    async def _post(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> Any:
        response = await client.post(self._url, headers=self._headers(), json=payload)
        if response.status_code >= 400:
            raise TrackerMCPError(
                f"Tracker MCP HTTP {response.status_code}: {response.text[:300]}"
            )
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        return self._parse_response(response)

    async def _handshake(self, client: httpx.AsyncClient) -> None:
        """MCP requires `initialize` + `notifications/initialized` before any call."""
        self._request_id += 1
        data = await self._post(
            client,
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "pm-agent", "version": "1.0"},
                },
            },
        )
        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            raise TrackerMCPError(
                f"Tracker MCP initialize {error.get('code')}: {error.get('message')}"
            )
        # Notification: no id, no response body expected.
        response = await client.post(
            self._url,
            headers=self._headers(),
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        if response.status_code >= 400:
            raise TrackerMCPError(
                f"Tracker MCP initialized notification HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

    async def _request_streamable(
        self,
        method: str,
        params: dict[str, Any] | None,
    ) -> Any:
        self._session_id = None
        async with self._new_client() as client:
            await self._handshake(client)
            self._request_id += 1
            data = await self._post(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": self._request_id,
                    "method": method,
                    "params": params or {},
                },
            )
        return data

    def _legacy_post_url(self, endpoint: str) -> str:
        post_url = urljoin(self._url, endpoint)
        source = urlsplit(self._url)
        target = urlsplit(post_url)
        if (source.scheme, source.netloc) != (target.scheme, target.netloc):
            raise TrackerMCPError("Tracker MCP SSE endpoint points to a different origin")
        return post_url

    @staticmethod
    async def _next_sse_data(lines: Any) -> str:
        async for line in lines:
            if line.startswith("data:"):
                return line[5:].strip()
        raise TrackerMCPError("Tracker MCP SSE stream ended unexpectedly")

    async def _legacy_rpc(
        self,
        client: httpx.AsyncClient,
        lines: Any,
        post_url: str,
        payload: dict[str, Any],
    ) -> Any:
        response = await client.post(
            post_url,
            headers=self._headers(accept="application/json"),
            json=payload,
        )
        if response.status_code >= 400:
            raise TrackerMCPError(
                f"Tracker MCP HTTP {response.status_code}: {response.text[:300]}"
            )
        if "id" not in payload:
            return None

        request_id = payload["id"]
        while True:
            raw_data = await self._next_sse_data(lines)
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("id") == request_id:
                return data

    async def _request_legacy_sse(
        self,
        method: str,
        params: dict[str, Any] | None,
    ) -> Any:
        async with self._new_client() as client:
            async with client.stream(
                "GET",
                self._url,
                headers=self._headers(
                    content_type=False,
                    accept="text/event-stream",
                ),
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode(errors="replace")
                    raise TrackerMCPError(
                        f"Tracker MCP SSE HTTP {response.status_code}: {body[:300]}"
                    )

                lines = response.aiter_lines()
                endpoint = await self._next_sse_data(lines)
                post_url = self._legacy_post_url(endpoint)

                self._request_id += 1
                initialized = await self._legacy_rpc(
                    client,
                    lines,
                    post_url,
                    {
                        "jsonrpc": "2.0",
                        "id": self._request_id,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "pm-agent", "version": "1.0"},
                        },
                    },
                )
                if isinstance(initialized, dict) and initialized.get("error"):
                    error = initialized["error"]
                    raise TrackerMCPError(
                        f"Tracker MCP initialize {error.get('code')}: "
                        f"{error.get('message')}"
                    )

                await self._legacy_rpc(
                    client,
                    lines,
                    post_url,
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {},
                    },
                )
                self._request_id += 1
                return await self._legacy_rpc(
                    client,
                    lines,
                    post_url,
                    {
                        "jsonrpc": "2.0",
                        "id": self._request_id,
                        "method": method,
                        "params": params or {},
                    },
                )

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self._uses_legacy_sse():
            data = await self._request_legacy_sse(method, params)
        else:
            data = await self._request_streamable(method, params)
        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            raise TrackerMCPError(
                f"Tracker MCP {error.get('code')}: {error.get('message')}"
            )
        return data.get("result") if isinstance(data, dict) else data

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
            parsed = json.loads(texts[0])
        except (TypeError, json.JSONDecodeError):
            return texts[0]
        if isinstance(parsed, dict) and parsed.get("error"):
            raise TrackerMCPError(str(parsed["error"]))
        return parsed


def _content_text(result: dict[str, Any]) -> str:
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


def _normalize_tool_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(arguments)
    if name == "CreateIssue":
        queue = get_config().tracker.tracker_queue.strip()
        if queue:
            normalized["queue"] = queue
    elif name == "ChangeIssueStatus":
        resolution = str(normalized.get("resolution") or "").strip().lower()
        status = str(
            normalized.get("status")
            or normalized.get("transition")
            or normalized.get("transition_id")
            or ""
        ).strip().lower()
        closes_issue = status in {"done", "closed", "close", "resolved", "закрыт", "закрыто"}
        if closes_issue and (
            not resolution
            or resolution in {"done", "closed", "close", "resolved", "решено", "закрыто"}
        ):
            normalized["resolution"] = "fixed"
    return normalized


async def register_tracker_mcp_tools() -> list[str]:
    """Discover remote Tracker tools and register local proxy Tool objects."""
    cfg = get_config().tracker_mcp
    if not cfg.tracker_mcp_url:
        logger.warning("Tracker MCP tools are disabled: set TRACKER_MCP_URL")
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
            arguments = _normalize_tool_arguments(_tool_name, kwargs)
            return await TrackerMCPClient().call_tool(_tool_name, arguments)

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
