"""
Yandex Tracker tools registered via @platform_tool for use by agents.

Risk levels:
  low    — read-only or non-destructive writes (get, search, comment, list transitions)
  medium — create / update / transition (except close)
  high   — close and other destructive workflow ends
"""

from __future__ import annotations

import re
from typing import Any, Literal

from core.assignee_resolver import load_team_users, resolve_assignee as match_assignee
from core.config import get_config
from core.tools import platform_tool
from core.tracker import TrackerClient, TrackerError
from core.tracker_tool_helpers import (
    build_find_fallback_queries,
    build_find_yql,
    build_patch_body,
    filter_issues_by_hint,
    format_assignee_yql,
    issue_summary,
    normalize_tracker_yql,
    parse_csv_logins,
    parse_custom_fields_json,
    parse_tags,
)


def _effective_queue(queue: str) -> str:
    return queue or get_config().tracker.tracker_queue


def _queue_from_issue_key(issue_key: str, fallback: str = "") -> str:
    if "-" in issue_key:
        return issue_key.split("-", 1)[0]
    return _effective_queue(fallback)


async def _resolve_login(
    name_or_login: str, client: TrackerClient, queue: str
) -> tuple[str, dict[str, Any]]:
    """Resolve display name to login via queue team; returns (login, meta)."""
    if not name_or_login.strip():
        return "", {}
    m = await match_assignee(name_or_login, client, queue)
    meta: dict[str, Any] = {
        "assignee_requested": name_or_login.strip(),
        "assignee_login": m.login,
        "assignee_matched_display": m.display,
        "assignee_match_score": round(m.score, 3),
    }
    return m.login, meta


async def _resolve_yql_assignees(
    yql: str, client: TrackerClient, queue: str
) -> str:
    """Replace Assignee: \"Name\" with resolved login in YQL."""
    pattern = re.compile(
        r'(Assignee:\s*)"([^"]+)"|Assignee:\s+([a-z0-9._-]+)',
        re.IGNORECASE,
    )

    async def repl(match: re.Match[str]) -> str:
        name = match.group(2) or match.group(3) or ""
        login, _ = await _resolve_login(name, client, queue)
        return format_assignee_yql(login)

    parts: list[str] = []
    last = 0
    for m in pattern.finditer(yql):
        parts.append(yql[last : m.start()])
        parts.append(await repl(m))
        last = m.end()
    parts.append(yql[last:])
    return "".join(parts) if parts else yql


@platform_tool(name="tracker_get_queue_meta", risk="low", scopes=["tracker:read"])
async def tracker_get_queue_meta(queue: str = "") -> dict[str, Any]:
    """
    Get queue metadata: issue types, priorities, local/custom field ids.
    Use before setting story points or custom fields.
    """
    q = _effective_queue(queue)
    async with TrackerClient() as client:
        return await client.get_queue_meta(q)


@platform_tool(name="tracker_list_team_members", risk="low", scopes=["tracker:read"])
async def tracker_list_team_members(queue: str = "") -> dict[str, Any]:
    """
    List queue team members (logins and display names).
    Use to pick assignee when the user names a person — match closest display/login.
    """
    q = _effective_queue(queue)
    async with TrackerClient() as client:
        users = await load_team_users(client, q)
    return {
        "queue": q,
        "count": len(users),
        "members": [
            {
                "login": u.login,
                "display": u.display,
                "first_name": u.first_name,
                "last_name": u.last_name,
            }
            for u in users
        ],
    }


@platform_tool(name="tracker_resolve_assignee", risk="low", scopes=["tracker:read"])
async def tracker_resolve_assignee(name: str, queue: str = "") -> dict[str, Any]:
    """Resolve a person's name or nickname to Tracker login (fuzzy match on queue team)."""
    q = _effective_queue(queue)
    async with TrackerClient() as client:
        login, meta = await _resolve_login(name, client, q)
    return {"name": name, "login": login, **meta}


@platform_tool(name="tracker_get_issue", risk="low", scopes=["tracker:read"])
async def tracker_get_issue(issue_key: str, detailed: bool = True) -> dict[str, Any]:
    """Get a Yandex Tracker issue by key (e.g. DARKHORSE-1)."""
    async with TrackerClient() as client:
        issue = await client.get_issue(issue_key)
    return issue_summary(issue, detailed=detailed)


@platform_tool(name="tracker_find_issues", risk="low", scopes=["tracker:read"])
async def tracker_find_issues(
    summary_hint: str = "",
    assignee: str = "",
    status: str = "",
    issue_key: str = "",
    queue: str = "",
    limit: int = 15,
) -> dict[str, Any]:
    """
    Find issues by context before update/close/transition.

    NOT for creating new tasks — use tracker_create_issue for «создай/заведи задачу».
    Use when the user mentions a task without key (e.g. «закрой CI», «задача Романа»).
    Filter by summary_hint (words from title), assignee (login), status.
    If issue_key is given (DARKHORSE-8), fetches that issue directly.
    Returns candidates — pick the best match, do not ask the user for a key.
    """
    q = _effective_queue(queue)
    key = issue_key.strip().upper()
    async with TrackerClient() as client:
        if key:
            try:
                issue = await client.get_issue(key)
                return {
                    "count": 1,
                    "query_used": f"key:{key}",
                    "issues": [issue_summary(issue, detailed=True)],
                }
            except TrackerError:
                return {
                    "count": 0,
                    "query_used": f"key:{key}",
                    "issues": [],
                    "not_found": True,
                    "message": f"Задача {key} не найдена",
                }

        assignee_login, _assignee_meta = await _resolve_login(assignee, client, q)
        yql = build_find_yql(
            summary_hint=summary_hint, assignee_login=assignee_login, status=status
        )
        issues = await client.search_issues(yql, queue=q, limit=limit)

        if not issues and assignee_login and summary_hint.strip():
            yql = build_find_yql(summary_hint=summary_hint, assignee_login="", status=status)
            issues = await client.search_issues(yql, queue=q, limit=limit)

        if not issues and summary_hint.strip():
            for word in summary_hint.split():
                if len(word) < 2:
                    continue
                broader = build_find_yql(
                    summary_hint=word, assignee_login=assignee_login, status=status
                )
                issues = await client.search_issues(broader, queue=q, limit=limit)
                if issues:
                    yql = broader
                    break
                if assignee_login:
                    broader = build_find_yql(summary_hint=word, assignee_login="", status=status)
                    issues = await client.search_issues(broader, queue=q, limit=limit)
                    if issues:
                        yql = broader
                        break

        if not issues:
            for fallback in build_find_fallback_queries(
                summary_hint=summary_hint,
                assignee_login=assignee_login,
                status=status,
            ):
                if fallback == yql:
                    continue
                issues = await client.search_issues(fallback, queue=q, limit=limit)
                if issues:
                    yql = fallback
                    break

    issues = filter_issues_by_hint(issues, summary_hint)
    result_issues = [issue_summary(i, detailed=True) for i in issues]
    out: dict[str, Any] = {
        "count": len(result_issues),
        "query_used": yql,
        "issues": result_issues,
    }
    if not result_issues:
        out["not_found"] = True
        out["message"] = "Задачи по указанному контексту не найдены"
    return out


@platform_tool(name="tracker_search_issues", risk="low", scopes=["tracker:read"])
async def tracker_search_issues(query: str, queue: str = "") -> dict[str, Any]:
    """
    Search Yandex Tracker issues using YQL (not SQL).

    Examples:
      query='Summary: "MCP" AND Assignee: shinkarenkorom'
      query='Assignee: shinkarenkorom'
      query='Status: Open'
    Do NOT use assignee = 'name' — use tracker_find_issues or Assignee: login.
    """
    q = _effective_queue(queue)
    async with TrackerClient() as client:
        yql = normalize_tracker_yql(query)
        yql = await _resolve_yql_assignees(yql, client, q)
        issues = await client.search_issues(yql, queue=q or None, limit=10)
    return {
        "count": len(issues),
        "issues": [issue_summary(i, detailed=False) for i in issues],
    }


@platform_tool(name="tracker_list_transitions", risk="low", scopes=["tracker:read"])
async def tracker_list_transitions(issue_key: str) -> dict[str, Any]:
    """List available workflow transitions for an issue (before changing status)."""
    async with TrackerClient() as client:
        transitions = await client.list_transitions(issue_key)
    return {
        "issue_key": issue_key,
        "transitions": [
            {"id": t.get("id"), "display": t.get("display"), "to": (t.get("to") or {}).get("display")}
            for t in transitions
        ],
    }


@platform_tool(name="tracker_create_issue", risk="medium", scopes=["tracker:write"])
async def tracker_create_issue(
    summary: str,
    queue: str = "",
    description: str = "",
    priority: str = "",
    assignee: str = "",
    issue_type: str = "",
    tags: str = "",
    deadline: str = "",
    story_points: str = "",
    sprint: str = "",
    parent: str = "",
    project: str = "",
    components: str = "",
    followers: str = "",
    custom_fields: str = "",
) -> dict[str, Any]:
    """
    Create a new issue in Yandex Tracker.

    Use when the user asks to CREATE/ADD a task (создай, заведи, поставь задачу).
    assignee: login or display name — matched to nearest queue team member.
    Optional: description, priority, issue_type, tags, deadline, story_points, sprint, parent, …
    """
    extra = parse_custom_fields_json(custom_fields)
    if "error" in extra:
        return extra

    sp_val: int | float | None = None
    if story_points:
        try:
            sp_f = float(story_points)
            sp_val = int(sp_f) if sp_f == int(sp_f) else sp_f
        except ValueError:
            return {"error": f"Invalid story_points: {story_points!r}"}

    q = _effective_queue(queue)
    async with TrackerClient() as client:
        assignee_login: str | None = None
        assignee_meta: dict[str, Any] = {}
        if assignee:
            assignee_login, assignee_meta = await _resolve_login(assignee, client, q)
        follower_logins: list[str] = []
        for name in parse_csv_logins(followers):
            flogin, _ = await _resolve_login(name, client, q)
            follower_logins.append(flogin)

        issue = await client.create_issue(
            queue=q,
            summary=summary,
            description=description or None,
            priority=priority or None,
            assignee=assignee_login,
            issue_type=issue_type or None,
            tags=parse_tags(tags) or None,
            deadline=deadline or None,
            followers=follower_logins or None,
            story_points=sp_val,
            sprint=sprint or None,
            parent=parent or None,
            project=project or None,
            components=parse_tags(components) or None,
            custom_fields=extra or None,
        )
    out = issue_summary(issue, detailed=True)
    out.update(assignee_meta)
    return out


@platform_tool(name="tracker_patch_issue", risk="medium", scopes=["tracker:write"])
async def tracker_patch_issue(
    issue_key: str,
    summary: str = "",
    description: str = "",
    priority: str = "",
    assignee: str = "",
    issue_type: str = "",
    tags: str = "",
    deadline: str = "",
    story_points: str = "",
    sprint: str = "",
    parent: str = "",
    project: str = "",
    components: str = "",
    custom_fields: str = "",
) -> dict[str, Any]:
    """
    Update fields of an existing issue. Only non-empty arguments are applied.
    custom_fields: JSON object, e.g. {"storyPoints": 5} or queue field id.
    """
    fields = build_patch_body(
        summary=summary,
        description=description,
        priority=priority,
        assignee=assignee,
        issue_type=issue_type,
        tags=tags,
        deadline=deadline,
        story_points=story_points,
        sprint=sprint,
        parent=parent,
        project=project,
        components=components,
        custom_fields=custom_fields,
    )
    if "error" in fields:
        return fields
    if not fields:
        return {"error": "No fields to update"}

    q = _queue_from_issue_key(issue_key)
    async with TrackerClient() as client:
        if fields.get("assignee"):
            login, meta = await _resolve_login(str(fields["assignee"]), client, q)
            fields["assignee"] = login
        issue = await client.patch_issue(issue_key, fields)
    out = issue_summary(issue, detailed=True)
    if fields.get("assignee"):
        out["assignee_login"] = fields["assignee"]
    return out


@platform_tool(name="tracker_update_issue", risk="medium", scopes=["tracker:write"])
async def tracker_update_issue(
    issue_key: str,
    summary: str = "",
    description: str = "",
    priority: str = "",
    assignee: str = "",
) -> dict[str, Any]:
    """Update basic fields (summary, description, priority, assignee). Prefer tracker_patch_issue for more fields."""
    return await tracker_patch_issue(
        issue_key,
        summary=summary,
        description=description,
        priority=priority,
        assignee=assignee,
    )


@platform_tool(name="tracker_update_followers", risk="medium", scopes=["tracker:write"])
async def tracker_update_followers(
    issue_key: str,
    logins: str,
    action: Literal["add", "remove", "set"] = "add",
) -> dict[str, Any]:
    """
    Add, remove, or set issue followers (observers). logins: comma-separated Yandex logins.
    """
    raw_logins = parse_csv_logins(logins)
    if not raw_logins:
        return {"error": "logins is required (comma-separated)"}

    q = _queue_from_issue_key(issue_key)
    async with TrackerClient() as client:
        login_list: list[str] = []
        for name in raw_logins:
            login, _ = await _resolve_login(name, client, q)
            login_list.append(login)
        if action == "add":
            issue = await client.followers_add(issue_key, login_list)
        elif action == "remove":
            issue = await client.followers_remove(issue_key, login_list)
        else:
            issue = await client.followers_set(issue_key, login_list)
    return issue_summary(issue, detailed=True)


@platform_tool(name="tracker_transition_issue", risk="medium", scopes=["tracker:write"])
async def tracker_transition_issue(
    issue_key: str,
    transition: str,
    resolution: str = "",
    comment: str = "",
) -> dict[str, Any]:
    """
    Change issue status via workflow transition (id or display name).
    Call tracker_list_transitions first. For close use tracker_close_issue instead.
    resolution/comment — if the transition requires them (e.g. Резолюция on close).
    """
    if transition.lower() in ("close", "closed", "закрыть", "закрыт"):
        return {
            "error": "Use tracker_close_issue for closing",
        }
    async with TrackerClient() as client:
        result = await client.transition_issue(
            issue_key,
            transition,
            resolution=resolution or None,
            comment=comment or None,
        )
        issue = await client.get_issue(issue_key)
    return {
        "issue_key": issue_key,
        "transition_result": result,
        "issue": issue_summary(issue, detailed=True),
    }


@platform_tool(name="tracker_link_issues", risk="medium", scopes=["tracker:write"])
async def tracker_link_issues(issue_key: str, parent_key: str) -> dict[str, Any]:
    """Set parent issue (make issue_key a subtask of parent_key)."""
    async with TrackerClient() as client:
        issue = await client.set_parent(issue_key, parent_key)
    return issue_summary(issue, detailed=True)


@platform_tool(name="tracker_comment_issue", risk="low", scopes=["tracker:write"])
async def tracker_comment_issue(issue_key: str, text: str) -> dict[str, Any]:
    """
    Add a comment to an issue — for new context from chat (blockers, problems, notes).
    Use when the user message contains info beyond field updates (VPN, bugs, «Имя: …»).
    """
    async with TrackerClient() as client:
        comment = await client.comment_issue(issue_key, text)
    return {
        "comment_id": comment.get("id"),
        "issue_key": issue_key,
        "text": text,
    }


@platform_tool(name="tracker_close_issue", risk="high", scopes=["tracker:write"])
async def tracker_close_issue(
    issue_key: str,
    resolution: str = "fixed",
    comment: str = "",
) -> dict[str, Any]:
    """
    Close a Yandex Tracker issue (executes the 'close' transition).
    resolution: required in many queues (default 'fixed'). Keys: fixed, wontFix, duplicate, etc.
    High-risk tool (close transition + resolution).
    """
    async with TrackerClient() as client:
        result = await client.transition_issue(
            issue_key,
            "close",
            resolution=resolution or None,
            comment=comment or None,
        )
        issue = await client.get_issue(issue_key)
    return {
        "issue_key": issue_key,
        "transitioned": True,
        "transition": result,
        "issue": issue_summary(issue, detailed=True),
    }


__all__ = [
    "tracker_get_queue_meta",
    "tracker_list_team_members",
    "tracker_resolve_assignee",
    "tracker_get_issue",
    "tracker_find_issues",
    "tracker_search_issues",
    "tracker_list_transitions",
    "tracker_create_issue",
    "tracker_patch_issue",
    "tracker_update_issue",
    "tracker_update_followers",
    "tracker_transition_issue",
    "tracker_link_issues",
    "tracker_comment_issue",
    "tracker_close_issue",
]
