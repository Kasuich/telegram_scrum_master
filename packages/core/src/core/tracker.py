"""
Async Yandex Tracker client (REST API v3).

Supports both organization types:
  - Yandex 360:       tracker_org_type="360"   → X-Org-ID header
  - Yandex Cloud Org: tracker_org_type="cloud" → X-Cloud-Org-ID header

Usage::

    async with TrackerClient() as client:
        issue = await client.create_issue("DARKHORSE", "Fix login bug", priority="critical")
        print(issue["key"])   # DARKHORSE-1
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from core.config import get_config
from core.exceptions import CoreError

logger = logging.getLogger(__name__)


class TrackerError(CoreError):
    """Yandex Tracker API error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class TrackerClient:
    """Async wrapper over Yandex Tracker REST API v3."""

    def __init__(
        self,
        token: str | None = None,
        org_id: str | None = None,
        org_type: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        # Only load config when at least one param is missing
        if token is None or org_id is None or org_type is None or base_url is None:
            cfg = get_config().tracker
            token = token or cfg.tracker_token
            org_id = org_id or cfg.tracker_org_id
            org_type = org_type or cfg.tracker_org_type
            base_url = base_url or cfg.tracker_api_base
        self._token = token
        self._org_id = org_id
        self._org_type = org_type.lower()
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Context manager / lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> TrackerClient:
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        org_header = "X-Cloud-Org-ID" if self._org_type == "cloud" else "X-Org-ID"
        return {
            "Authorization": f"OAuth {self._token}",
            org_header: self._org_id,
            "Content-Type": "application/json",
        }

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base}/{path.lstrip('/')}"
        response = await self._http.request(method, url, headers=self._headers(), **kwargs)
        if response.status_code == 403:
            raise TrackerError(f"Access denied: {response.text[:200]}", status_code=403)
        if response.status_code == 404:
            raise TrackerError(f"Not found: {url}", status_code=404)
        if response.status_code == 422:
            raise TrackerError(f"Validation error: {response.text[:300]}", status_code=422)
        if response.status_code >= 400:
            raise TrackerError(
                f"HTTP {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
            )
        if response.status_code == 204:
            return None
        return response.json()

    # ------------------------------------------------------------------
    # Issues
    # ------------------------------------------------------------------

    async def create_issue(
        self,
        queue: str,
        summary: str,
        *,
        description: str | None = None,
        priority: str | None = None,
        assignee: str | None = None,
        issue_type: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new issue and return its full representation."""
        body: dict[str, Any] = {"queue": queue, "summary": summary}
        if description:
            body["description"] = description
        if priority:
            body["priority"] = priority
        if assignee:
            body["assignee"] = assignee
        if issue_type:
            body["type"] = issue_type
        if tags:
            body["tags"] = tags
        return await self._request("POST", "/issues/", json=body)

    async def get_issue(self, issue_key: str) -> dict[str, Any]:
        """Return issue by key, e.g. 'DARKHORSE-1'."""
        return await self._request("GET", f"/issues/{issue_key}")

    async def update_issue(self, issue_key: str, **fields: Any) -> dict[str, Any]:
        """Patch arbitrary issue fields."""
        return await self._request("PATCH", f"/issues/{issue_key}", json=fields)

    async def comment_issue(self, issue_key: str, text: str) -> dict[str, Any]:
        """Add a comment to an issue."""
        return await self._request("POST", f"/issues/{issue_key}/comments", json={"text": text})

    async def search_issues(
        self,
        query: str,
        *,
        queue: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Search issues using Yandex Query Language (YQL).

        Examples::
            await client.search_issues('Queue: DARKHORSE AND Status: "In Progress"')
            await client.search_issues("assignee: me()", queue="DARKHORSE")
        """
        yql = query
        if queue and "Queue:" not in query:
            yql = f'Queue: "{queue}" AND ({query})'
        result = await self._request(
            "POST",
            f"/issues/_search?perPage={limit}",
            json={"query": yql},
        )
        return result if isinstance(result, list) else []

    async def transition_issue(self, issue_key: str, transition_id: str) -> dict[str, Any]:
        """Execute a workflow transition (e.g. 'close', 'inProgress')."""
        transitions = await self._request("GET", f"/issues/{issue_key}/transitions")
        match = next(
            (
                t
                for t in transitions
                if t.get("id") == transition_id
                or t.get("display", "").lower() == transition_id.lower()
            ),
            None,
        )
        if match is None:
            available = [t.get("id") for t in transitions]
            raise TrackerError(f"Transition {transition_id!r} not found. Available: {available}")
        return await self._request(
            "POST", f"/issues/{issue_key}/transitions/{match['id']}/_execute"
        )

    # ------------------------------------------------------------------
    # Queues
    # ------------------------------------------------------------------

    async def get_queue(self, queue_key: str) -> dict[str, Any]:
        """Return queue metadata."""
        return await self._request("GET", f"/queues/{queue_key}")

    async def list_queues(self) -> list[dict[str, Any]]:
        """List all accessible queues."""
        result = await self._request("GET", "/queues/")
        return result if isinstance(result, list) else []


__all__ = ["TrackerClient", "TrackerError"]
