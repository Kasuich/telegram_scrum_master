"""
Yandex Tracker tools registered via @platform_tool for use by agents.

Risk levels:
  low    — read-only or non-destructive writes (get, search, comment)
  medium — create / update issues
  high   — transitions that close/delete work
"""

from __future__ import annotations

from typing import Any

from core.tools import platform_tool
from core.tracker import TrackerClient


@platform_tool(name="tracker_get_issue", risk="low", scopes=["tracker:read"])
async def tracker_get_issue(issue_key: str) -> dict[str, Any]:
    """Get a Yandex Tracker issue by key (e.g. DARKHORSE-1)."""
    async with TrackerClient() as client:
        issue = await client.get_issue(issue_key)
    return {
        "key": issue.get("key"),
        "summary": issue.get("summary"),
        "status": issue.get("status", {}).get("display"),
        "priority": issue.get("priority", {}).get("display"),
        "assignee": (issue.get("assignee") or {}).get("display"),
        "description": issue.get("description"),
    }


@platform_tool(name="tracker_search_issues", risk="low", scopes=["tracker:read"])
async def tracker_search_issues(query: str, queue: str = "") -> dict[str, Any]:
    """
    Search Yandex Tracker issues using YQL.

    Examples:
      query="Status: Open", queue="DARKHORSE"
      query='assignee: me() AND Status: "In Progress"'
    """
    async with TrackerClient() as client:
        issues = await client.search_issues(query, queue=queue or None, limit=10)
    return {
        "count": len(issues),
        "issues": [
            {
                "key": i.get("key"),
                "summary": i.get("summary"),
                "status": i.get("status", {}).get("display"),
                "priority": i.get("priority", {}).get("display"),
            }
            for i in issues
        ],
    }


@platform_tool(name="tracker_create_issue", risk="medium", scopes=["tracker:write"])
async def tracker_create_issue(
    summary: str,
    queue: str = "",
    description: str = "",
    priority: str = "",
    assignee: str = "",
) -> dict[str, Any]:
    """
    Create a new issue in Yandex Tracker.

    Args:
        summary: Issue title (required).
        queue: Queue key, e.g. DARKHORSE. Defaults to configured queue.
        description: Detailed description (optional).
        priority: blocker / critical / major / normal / minor (optional).
        assignee: Yandex login of assignee (optional).
    """
    from core.config import get_config

    effective_queue = queue or get_config().tracker.tracker_queue
    async with TrackerClient() as client:
        issue = await client.create_issue(
            queue=effective_queue,
            summary=summary,
            description=description or None,
            priority=priority or None,
            assignee=assignee or None,
        )
    return {
        "key": issue.get("key"),
        "summary": issue.get("summary"),
        "url": f"https://tracker.yandex.ru/{issue.get('key')}",
        "status": issue.get("status", {}).get("display"),
    }


@platform_tool(name="tracker_update_issue", risk="medium", scopes=["tracker:write"])
async def tracker_update_issue(
    issue_key: str,
    summary: str = "",
    description: str = "",
    priority: str = "",
    assignee: str = "",
) -> dict[str, Any]:
    """
    Update fields of an existing Yandex Tracker issue.

    Only non-empty arguments are applied.
    """
    fields: dict[str, Any] = {}
    if summary:
        fields["summary"] = summary
    if description:
        fields["description"] = description
    if priority:
        fields["priority"] = priority
    if assignee:
        fields["assignee"] = assignee

    if not fields:
        return {"error": "No fields to update"}

    async with TrackerClient() as client:
        issue = await client.update_issue(issue_key, **fields)
    return {
        "key": issue.get("key"),
        "summary": issue.get("summary"),
        "status": issue.get("status", {}).get("display"),
    }


@platform_tool(name="tracker_comment_issue", risk="low", scopes=["tracker:write"])
async def tracker_comment_issue(issue_key: str, text: str) -> dict[str, Any]:
    """Add a comment to a Yandex Tracker issue."""
    async with TrackerClient() as client:
        comment = await client.comment_issue(issue_key, text)
    return {
        "comment_id": comment.get("id"),
        "issue_key": issue_key,
        "text": text,
    }


@platform_tool(name="tracker_close_issue", risk="high", scopes=["tracker:write"])
async def tracker_close_issue(issue_key: str) -> dict[str, Any]:
    """
    Close a Yandex Tracker issue (executes the 'close' transition).

    This is a high-risk action — requires confirmation by default.
    """
    async with TrackerClient() as client:
        result = await client.transition_issue(issue_key, "close")
    return {
        "issue_key": issue_key,
        "transitioned": True,
        "transition": result,
    }


__all__ = [
    "tracker_get_issue",
    "tracker_search_issues",
    "tracker_create_issue",
    "tracker_update_issue",
    "tracker_comment_issue",
    "tracker_close_issue",
]
