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


def _norm_transition_label(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _transition_aliases(value: str) -> set[str]:
    normalized = _norm_transition_label(value)
    aliases = {normalized}
    if normalized in {"in_progress", "inprogress", "in progress", "в работе", "в работу"}:
        aliases.update(
            {
                "in_progress",
                "inprogress",
                "in progress",
                "в работе",
                "в работу",
                "взять в работу",
                "начать работу",
            }
        )
    if normalized in {"close", "closed", "done", "resolved", "закрыть", "закрыто", "закрыт"}:
        aliases.update(
            {
                "close",
                "closed",
                "done",
                "resolved",
                "закрыть",
                "закрыто",
                "закрыт",
                "решено",
                "завершено",
            }
        )
    return aliases


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

    def _ensure_configured(self) -> None:
        if not self._token:
            raise TrackerError("Yandex Tracker token is not configured. Set TRACKER_TOKEN.")
        if not self._org_id:
            raise TrackerError(
                "Yandex Tracker organization ID is not configured. Set TRACKER_ORG_ID."
            )

    async def __aenter__(self) -> TrackerClient:
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

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
        self._ensure_configured()
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
    # Issues — read / write
    # ------------------------------------------------------------------

    async def get_issue(self, issue_key: str, *, fields: str | None = None) -> dict[str, Any]:
        """Return issue by key, e.g. 'DARKHORSE-1'. Optional comma-separated fields filter."""
        params = {"fields": fields} if fields else None
        return await self._request("GET", f"/issues/{issue_key}", params=params)

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
        deadline: str | None = None,
        followers: list[str] | None = None,
        story_points: int | float | None = None,
        sprint: str | list[str] | None = None,
        parent: str | None = None,
        project: str | None = None,
        components: list[str] | None = None,
        custom_fields: dict[str, Any] | None = None,
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
        if deadline:
            body["deadline"] = deadline
        if followers:
            body["followers"] = followers
        if story_points is not None:
            body["storyPoints"] = story_points
        if sprint is not None:
            body["sprint"] = sprint
        if parent:
            body["parent"] = parent
        if project:
            body["project"] = project
        if components:
            body["components"] = components
        if custom_fields:
            body.update(custom_fields)
        return await self._request("POST", "/issues/", json=body)

    async def patch_issue(self, issue_key: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Patch issue fields (supports add/remove operators for array fields)."""
        if not fields:
            raise TrackerError("patch_issue requires at least one field")
        return await self._request("PATCH", f"/issues/{issue_key}", json=fields)

    async def update_issue(self, issue_key: str, **fields: Any) -> dict[str, Any]:
        """Patch arbitrary issue fields (alias for patch_issue with kwargs)."""
        return await self.patch_issue(issue_key, fields)

    async def comment_issue(self, issue_key: str, text: str) -> dict[str, Any]:
        """Add a comment to an issue."""
        return await self._request("POST", f"/issues/{issue_key}/comments", json={"text": text})

    async def list_comments(self, issue_key: str, *, per_page: int = 50) -> list[dict[str, Any]]:
        """Return comments of an issue (oldest first)."""
        result = await self._request("GET", f"/issues/{issue_key}/comments?perPage={per_page}")
        return result if isinstance(result, list) else []

    # ------------------------------------------------------------------
    # Sprints
    # ------------------------------------------------------------------

    async def create_sprint(
        self,
        *,
        name: str,
        board_id: str,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        """Create a sprint on a Tracker board and return its representation."""
        body: dict[str, Any] = {
            "name": name,
            "board": {"id": str(board_id)},
            "startDate": start_date,
            "endDate": end_date,
        }
        return await self._request("POST", "/sprints", json=body)

    async def get_sprint(self, sprint_id: str) -> dict[str, Any]:
        """Return sprint by ID."""
        return await self._request("GET", f"/sprints/{sprint_id}")

    async def list_sprints(self, board_id: str) -> list[dict[str, Any]]:
        """Return all sprints of a board."""
        result = await self._request("GET", f"/boards/{board_id}/sprints")
        return result if isinstance(result, list) else []

    async def list_boards(self) -> list[dict[str, Any]]:
        """Return all accessible Agile boards."""
        result = await self._request("GET", "/boards")
        return result if isinstance(result, list) else []

    async def get_board(self, board_id: str) -> dict[str, Any]:
        """Return Agile board parameters including its query/filter settings."""
        result = await self._request("GET", f"/boards/{board_id}")
        return result if isinstance(result, dict) else {}

    async def add_issue_to_sprint(
        self,
        issue_key: str,
        sprint_id: str,
        *,
        preserve_existing: bool = True,
    ) -> dict[str, Any]:
        """Add an issue to a sprint by patching the issue's sprint field."""
        sprint_items = [{"id": str(sprint_id)}]
        if preserve_existing:
            issue = await self.get_issue(issue_key)
            current = issue.get("sprint") if isinstance(issue, dict) else []
            seen = {str(sprint_id)}
            sprint_items = []
            if isinstance(current, list):
                for item in current:
                    if not isinstance(item, dict) or item.get("id") is None:
                        continue
                    sid = str(item["id"])
                    if sid in seen:
                        continue
                    sprint_items.append({"id": sid})
                    seen.add(sid)
            sprint_items.append({"id": str(sprint_id)})
        return await self.patch_issue(issue_key, {"sprint": sprint_items})

    async def search_issues(
        self,
        query: str,
        *,
        queue: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search issues using Yandex Query Language (YQL)."""
        yql = query
        if queue and "Queue:" not in query and "queue:" not in query.lower():
            yql = f'Queue: "{queue}" AND ({query})'
        result = await self._request(
            "POST",
            f"/issues/_search?perPage={limit}",
            json={"query": yql},
        )
        return result if isinstance(result, list) else []

    async def search_all_issues(
        self,
        query: str,
        *,
        queue: str | None = None,
        page_size: int = 200,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch ALL issues matching a YQL query, paginating automatically.

        Uses perPage + page to iterate. Stops when a page returns fewer
        than page_size results or max_pages is reached.
        """
        yql = query
        if queue and "Queue:" not in query and "queue:" not in query.lower():
            yql = f'Queue: "{queue}" AND ({query})'
        all_issues: list[dict[str, Any]] = []
        for page_num in range(max_pages):
            result = await self._request(
                "POST",
                f"/issues/_search?perPage={page_size}&page={page_num + 1}",
                json={"query": yql},
            )
            page_issues = result if isinstance(result, list) else []
            all_issues.extend(page_issues)
            if len(page_issues) < page_size:
                break
        return all_issues

    # ------------------------------------------------------------------
    # Followers
    # ------------------------------------------------------------------

    async def followers_add(self, issue_key: str, logins: list[str]) -> dict[str, Any]:
        return await self.patch_issue(issue_key, {"followers": {"add": logins}})

    async def followers_remove(self, issue_key: str, logins: list[str]) -> dict[str, Any]:
        return await self.patch_issue(issue_key, {"followers": {"remove": logins}})

    async def followers_set(self, issue_key: str, logins: list[str]) -> dict[str, Any]:
        return await self.patch_issue(issue_key, {"followers": logins})

    # ------------------------------------------------------------------
    # Workflow transitions
    # ------------------------------------------------------------------

    async def list_transitions(self, issue_key: str) -> list[dict[str, Any]]:
        """List available workflow transitions for an issue."""
        result = await self._request("GET", f"/issues/{issue_key}/transitions")
        return result if isinstance(result, list) else []

    async def list_resolutions(self) -> list[dict[str, Any]]:
        """List organization resolution types (for close transitions)."""
        result = await self._request("GET", "/resolutions/")
        return result if isinstance(result, list) else []

    async def transition_issue(
        self,
        issue_key: str,
        transition_id: str,
        *,
        resolution: str | None = None,
        comment: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a workflow transition by id/display or target status display."""
        transitions = await self.list_transitions(issue_key)
        target_aliases = _transition_aliases(transition_id)

        def matches_transition(transition: dict[str, Any]) -> bool:
            labels = {
                _norm_transition_label(transition.get("id")),
                _norm_transition_label(transition.get("display")),
                _norm_transition_label((transition.get("to") or {}).get("key")),
                _norm_transition_label((transition.get("to") or {}).get("display")),
            }
            if labels & target_aliases:
                return True
            return any(
                alias and any(alias in label for label in labels if label)
                for alias in target_aliases
            )

        match = next(
            (t for t in transitions if matches_transition(t)),
            None,
        )
        if match is None:
            available = [
                {
                    "id": t.get("id"),
                    "display": t.get("display"),
                    "to": (t.get("to") or {}).get("display"),
                }
                for t in transitions
            ]
            raise TrackerError(f"Transition {transition_id!r} not found. Available: {available}")
        body: dict[str, Any] = dict(extra_fields or {})
        if resolution:
            body["resolution"] = resolution
        if comment:
            body["comment"] = comment
        return await self._request(
            "POST",
            f"/issues/{issue_key}/transitions/{match['id']}/_execute",
            json=body if body else None,
        )

    # ------------------------------------------------------------------
    # Links
    # ------------------------------------------------------------------

    async def set_parent(self, issue_key: str, parent_key: str) -> dict[str, Any]:
        """Set parent issue (subtask relationship)."""
        return await self.patch_issue(issue_key, {"parent": parent_key})

    # ------------------------------------------------------------------
    # Queues & metadata
    # ------------------------------------------------------------------

    async def get_queue(self, queue_key: str, *, expand: str | None = None) -> dict[str, Any]:
        """Return queue metadata. Use expand='team' for teamUsers."""
        params = {"expand": expand} if expand else None
        return await self._request("GET", f"/queues/{queue_key}", params=params)

    async def list_users(self, *, per_page: int = 100, page: int = 1) -> list[dict[str, Any]]:
        """List organization users (paginated)."""
        result = await self._request(
            "GET",
            f"/users/?perPage={per_page}&page={page}",
        )
        return result if isinstance(result, list) else []

    async def list_queues(self) -> list[dict[str, Any]]:
        """List all accessible queues."""
        result = await self._request("GET", "/queues/")
        return result if isinstance(result, list) else []

    async def get_queue_local_fields(self, queue_key: str) -> list[dict[str, Any]]:
        """Return local (queue-specific) field definitions."""
        try:
            result = await self._request("GET", f"/queues/{queue_key}/localFields")
            return result if isinstance(result, list) else []
        except TrackerError as exc:
            if exc.status_code == 404:
                return []
            raise

    async def get_queue_meta(self, queue_key: str) -> dict[str, Any]:
        """
        Combined queue metadata for agents: queue info, issue types, priorities, local fields.
        """
        queue = await self.get_queue(queue_key)
        local_fields = await self.get_queue_local_fields(queue_key)

        def _summarize_options(items: Any) -> list[dict[str, str]]:
            if not isinstance(items, list):
                return []
            out: list[dict[str, str]] = []
            for item in items:
                if isinstance(item, dict):
                    out.append(
                        {
                            "id": str(item.get("id", "")),
                            "key": str(item.get("key", "")),
                            "name": str(item.get("name", item.get("display", ""))),
                        }
                    )
                elif isinstance(item, str):
                    out.append({"key": item, "name": item})
            return out

        issue_types = _summarize_options(queue.get("issueTypes") or queue.get("issueTypesConfig"))
        priorities = _summarize_options(queue.get("priorities") or queue.get("priority"))
        resolutions_raw = await self.list_resolutions()
        resolutions = _summarize_options(resolutions_raw)

        field_catalog = [
            {
                "id": f.get("id"),
                "name": f.get("name"),
                "type": (f.get("schema") or {}).get("type"),
            }
            for f in local_fields
            if isinstance(f, dict)
        ]

        return {
            "queue_key": queue.get("key", queue_key),
            "queue_name": queue.get("name"),
            "issue_types": issue_types,
            "priorities": priorities,
            "resolutions": resolutions,
            "local_fields": field_catalog,
            "hint": (
                "Use standard keys: summary, description, assignee, priority, type, tags, "
                "deadline, storyPoints, sprint, parent, followers. "
                "On close pass resolution (default fixed) via tracker_close_issue. "
                "Custom queue fields use their id from local_fields in custom_fields."
            ),
        }


__all__ = ["TrackerClient", "TrackerError"]
