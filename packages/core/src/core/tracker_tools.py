"""
Yandex Tracker tools registered via @platform_tool for use by agents.

Risk levels:
  low    — read-only or non-destructive writes (get, search, comment, list transitions)
  medium — create / update / transition (except close)
  high   — close and other destructive workflow ends
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Literal

from core.assignee_resolver import (
    load_team_users,
)
from core.assignee_resolver import (
    resolve_assignee as match_assignee,
)
from core.config import get_config
from core.invocation import get_current_invocation_context
from core.issue_dedup import (
    PlannedIssueForDedup,
    apply_duplicate_merge,
    dedup_enabled_for_create,
    resolve_planned_issues_dedup,
)
from core.tools import platform_tool
from core.tracker import TrackerClient, TrackerError
from core.tracker_tool_helpers import (
    apply_open_status_filter_to_yql,
    build_find_fallback_queries,
    build_find_yql,
    build_patch_body,
    filter_issues_by_hint,
    filter_terminal_issues,
    format_assignee_yql,
    issue_summary,
    normalize_deadline,
    normalize_tracker_yql,
    parse_csv_logins,
    parse_custom_fields_json,
    parse_tags,
)

_QUEUE_PLACEHOLDERS = frozenset({"default"})


def _effective_queue(queue: str) -> str:
    """Resolve queue: empty or LLM placeholders → TRACKER_QUEUE from config."""
    q = (queue or "").strip()
    if not q or q.lower() in _QUEUE_PLACEHOLDERS:
        return get_config().tracker.tracker_queue
    return q


def _queue_from_issue_key(issue_key: str, fallback: str = "") -> str:
    if "-" in issue_key:
        return issue_key.split("-", 1)[0]
    return _effective_queue(fallback)


def _norm_name(value: str) -> str:
    return " ".join(value.casefold().split())


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _extract_issue_key(*values: str) -> str:
    for value in values:
        match = re.search(r"\b([A-Z][A-Z0-9_]+-\d+)\b", value.upper())
        if match:
            return match.group(1)
    return ""


_LEAD_ADMIN_ROLES = frozenset({"lead", "admin"})


def _actor_role() -> str:
    ctx = get_current_invocation_context()
    return str(ctx.actor_role or "").strip() if ctx is not None else ""


def _require_lead_or_admin(action: str) -> dict[str, Any] | None:
    role = _actor_role().casefold()
    if role in _LEAD_ADMIN_ROLES:
        return None
    return {
        "error": f"{action} is allowed only for team lead/admin",
        "required_roles": sorted(_LEAD_ADMIN_ROLES),
        "actor_role": role or None,
    }


def _default_board_from_context() -> tuple[str, str]:
    ctx = get_current_invocation_context()
    if ctx is None:
        return "", ""
    board_id = str(ctx.actor_default_board_id or "").strip()
    board_name = str((ctx.actor_settings or {}).get("default_board_name") or "").strip()
    return board_id, board_name


def _with_default_board(board_id: str, board_name: str) -> tuple[str, str]:
    if board_id.strip() or board_name.strip():
        return board_id.strip(), board_name.strip()
    return _default_board_from_context()


def _is_epic_type(issue_type: str) -> bool:
    return issue_type.strip().casefold() == "epic"


def _sprint_summary(
    sprint: dict[str, Any],
    *,
    fallback_board_id: str = "",
    fallback_board_name: str = "",
) -> dict[str, Any]:
    board = sprint.get("board") or {}
    return {
        "id": sprint.get("id"),
        "name": sprint.get("name"),
        "status": sprint.get("status"),
        "archived": sprint.get("archived"),
        "board_id": board.get("id") or fallback_board_id,
        "board": board.get("display") or fallback_board_name,
        "start_date": sprint.get("startDate"),
        "end_date": sprint.get("endDate"),
        "url": sprint.get("self"),
        "raw": sprint,
    }


def _parse_sprint_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _next_sprint_name(name: str) -> str:
    text = name.strip()
    if not text:
        return "Next sprint"
    match = re.search(r"(\d+)(?!.*\d)", text)
    if match is None:
        return f"{text} next"
    value = str(int(match.group(1)) + 1)
    return f"{text[:match.start()]}{value}{text[match.end():]}"


def _next_sprint_dates(sprint: dict[str, Any]) -> tuple[str, str] | None:
    start = _parse_sprint_date(sprint.get("startDate"))
    end = _parse_sprint_date(sprint.get("endDate"))
    if start is None or end is None or end < start:
        return None
    days = (end - start).days
    next_start = end + timedelta(days=1)
    next_end = next_start + timedelta(days=days)
    return next_start.isoformat(), next_end.isoformat()


def _issue_has_sprint(issue: dict[str, Any], sprint_id: str) -> bool:
    sprint_items = issue.get("sprint")
    if not isinstance(sprint_items, list):
        return False
    return any(
        isinstance(item, dict) and str(item.get("id")) == str(sprint_id)
        for item in sprint_items
    )


def _is_closed_issue(issue: dict[str, Any]) -> bool:
    status = issue.get("status") or {}
    values = [
        status.get("key") if isinstance(status, dict) else "",
        status.get("display") if isinstance(status, dict) else status,
    ]
    closed = {"closed", "close", "закрыт", "закрыта", "закрыто", "закрытые"}
    for value in values:
        normalized = " ".join(str(value or "").casefold().replace("ё", "е").split())
        if normalized in closed:
            return True
    return False


def _sprint_sort_key(sprint: dict[str, Any]) -> date:
    return _parse_sprint_date(sprint.get("endDate")) or date.min


def _select_current_sprint(sprints: list[dict[str, Any]]) -> dict[str, Any] | None:
    today = date.today()
    active = [s for s in sprints if not bool(s.get("archived"))]
    in_window = []
    for sprint in active:
        start = _parse_sprint_date(sprint.get("startDate"))
        end = _parse_sprint_date(sprint.get("endDate"))
        if start is not None and end is not None and start <= today <= end:
            in_window.append(sprint)
    if in_window:
        return sorted(in_window, key=_sprint_sort_key, reverse=True)[0]
    if active:
        return sorted(active, key=_sprint_sort_key, reverse=True)[0]
    return None


async def _resolve_board_id(
    client: TrackerClient,
    *,
    board_id: str = "",
    board_name: str = "",
) -> tuple[str, dict[str, Any]]:
    if board_id.strip():
        return board_id.strip(), {}
    target = _norm_name(board_name)
    if not target:
        raise TrackerError("board_id or board_name is required")

    boards = await client.list_boards()
    exact = [b for b in boards if _norm_name(str(b.get("name", ""))) == target]
    if len(exact) == 1:
        board = exact[0]
        return str(board.get("id")), {"board_name": board.get("name")}
    if len(exact) > 1:
        raise TrackerError(
            f"Board name {board_name!r} is ambiguous. Matches: "
            f"{[{'id': b.get('id'), 'name': b.get('name')} for b in exact]}"
        )

    contains = [b for b in boards if target in _norm_name(str(b.get("name", "")))]
    if len(contains) == 1:
        board = contains[0]
        return str(board.get("id")), {"board_name": board.get("name"), "board_match": "contains"}
    if contains:
        raise TrackerError(
            f"Board name {board_name!r} is ambiguous. Matches: "
            f"{[{'id': b.get('id'), 'name': b.get('name')} for b in contains]}"
        )
    raise TrackerError(f"Board {board_name!r} not found")


async def _resolve_sprint_id(
    client: TrackerClient,
    *,
    sprint_id: str = "",
    sprint_name: str = "",
    board_id: str = "",
    board_name: str = "",
) -> tuple[str, dict[str, Any]]:
    if sprint_id.strip():
        return sprint_id.strip(), {}
    target = _norm_name(sprint_name)
    if not target:
        raise TrackerError("sprint_id or sprint_name is required")

    board_meta: dict[str, Any] = {}
    board_ids: list[str] = []
    if board_id.strip() or board_name.strip():
        resolved_board_id, board_meta = await _resolve_board_id(
            client, board_id=board_id, board_name=board_name
        )
        board_ids = [resolved_board_id]
    else:
        boards = await client.list_boards()
        board_ids = [str(b.get("id")) for b in boards if b.get("id") is not None]

    matches: list[dict[str, Any]] = []
    for bid in board_ids:
        for sprint in await client.list_sprints(bid):
            if _norm_name(str(sprint.get("name", ""))) == target:
                matches.append({"board_id": bid, **sprint})

    if len(matches) == 1:
        sprint = matches[0]
        board = sprint.get("board") or {}
        return str(sprint.get("id")), {
            **board_meta,
            "sprint_name": sprint.get("name"),
            "board_id": board.get("id") or sprint.get("board_id"),
            "board": board.get("display") or board_meta.get("board_name"),
        }
    if len(matches) > 1:
        match_summaries = [
            {"id": s.get("id"), "name": s.get("name"), "board_id": s.get("board_id")}
            for s in matches
        ]
        raise TrackerError(
            f"Sprint name {sprint_name!r} is ambiguous. Provide board_id/board_name. "
            f"Matches: {match_summaries}"
        )
    raise TrackerError(f"Sprint {sprint_name!r} not found")


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


async def _resolve_yql_assignees(yql: str, client: TrackerClient, queue: str) -> str:
    """Replace Assignee: \"Name\" with resolved login in YQL."""
    pattern = re.compile(
        # Skip YQL functions like empty() / notEmpty() — only resolve plain names/logins
        r'(Assignee:\s*)"([^"]+)"|Assignee:\s+(?!empty\(\)|notEmpty\(\))([a-z0-9._-]+)',
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
    query: str = "",
    queue: str = "",
    limit: int = 15,
) -> dict[str, Any]:
    """
    Find issues by context before update/close/transition.

    NOT for creating new tasks — use tracker_create_issue for «создай/заведи задачу».
    Use when the user mentions a task without key (e.g. «закрой CI», «задача Романа»).
    Filter by summary_hint (words from title), assignee (login), status.
    By default excludes closed and cancelled issues; pass status= to search a specific one.
    Set TRACKER_SEARCH_ALL_STATUSES=true to search every status.
    If issue_key is given (DARKHORSE-8), fetches that issue directly.
    Returns candidates — pick the best match, do not ask the user for a key.
    """
    q = _effective_queue(queue)
    key = _extract_issue_key(issue_key, query, summary_hint, status)
    if query and not summary_hint and not status:
        summary_hint = query
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

    issues = filter_terminal_issues(issues, explicit_status=status)
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
      query='Assignee: empty()'                         -- tasks with NO assignee
      query='Assignee: empty() AND Status: Open'        -- open tasks without assignee
      query='Deadline: < now() AND Status: Open'        -- overdue open tasks
    Do NOT use assignee = 'name' — use tracker_find_issues or Assignee: login.
    By default excludes closed/cancelled unless Status: is present in the query.
    Use Assignee: empty() to find unassigned tasks (NOT Assignee: "" or Assignee: null).
    """
    q = _effective_queue(queue)
    async with TrackerClient() as client:
        yql = normalize_tracker_yql(query)
        yql = apply_open_status_filter_to_yql(yql)
        yql = await _resolve_yql_assignees(yql, client, q)
        issues = await client.search_issues(yql, queue=q or None, limit=10)
    issues = filter_terminal_issues(issues)
    return {
        "count": len(issues),
        "query_used": yql,
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
            {
                "id": t.get("id"),
                "display": t.get("display"),
                "to": (t.get("to") or {}).get("display"),
            }
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
    allow_duplicate: bool = False,
) -> dict[str, Any]:
    """
    Create a new issue in Yandex Tracker.

    Use when the user asks to CREATE/ADD a task (создай, заведи, поставь задачу).
    assignee: login or display name — matched to nearest queue team member.
    Optional: description, priority, issue_type, tags, deadline, story_points, sprint, parent, …
    When a duplicate is found, updates the existing issue (comment/status/fields)
    instead of creating.
    Set allow_duplicate=true ONLY on explicit user request to create a second copy.
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
    if _is_epic_type(issue_type):
        forbidden = _require_lead_or_admin("Epic creation")
        if forbidden is not None:
            return forbidden

    async with TrackerClient() as client:
        assignee_login: str | None = None
        assignee_meta: dict[str, Any] = {}
        if assignee:
            assignee_login, assignee_meta = await _resolve_login(assignee, client, q)
        follower_logins: list[str] = []
        for name in parse_csv_logins(followers):
            flogin, _ = await _resolve_login(name, client, q)
            follower_logins.append(flogin)

        deadline_val: str | None = None
        if deadline:
            normalized = normalize_deadline(deadline)
            if isinstance(normalized, dict):
                return normalized
            deadline_val = normalized

        resolved_type = issue_type or None
        if dedup_enabled_for_create() and not allow_duplicate:
            planned = PlannedIssueForDedup(
                planned_id="0",
                summary=summary,
                issue_type=issue_type or "",
                parent_key=parent.strip() or None,
                description=description or "",
                deadline=deadline_val,
                priority=priority or None,
            )
            resolutions, by_key = await resolve_planned_issues_dedup(client, q, [planned])
            res = resolutions[0]
            if res.action == "merge" and res.duplicate_key:
                existing = by_key.get(res.duplicate_key)
                if not existing:
                    existing = await client.get_issue(res.duplicate_key)
                out = await apply_duplicate_merge(
                    client,
                    res.duplicate_key,
                    existing,
                    planned=planned,
                    description=description or "",
                    comment=res.comment,
                    target_status=res.target_status,
                    deadline=deadline_val,
                    priority=priority or None,
                    assignee=assignee_login,
                    story_points=sp_val,
                )
                out.update(assignee_meta)
                applied = ", ".join(out.get("updates_applied") or []) or "без изменений"
                out["message"] = (
                    f"Не создавал новую — обновил существующую {res.duplicate_key} ({applied})."
                )
                if res.reason:
                    out["dedup_reason"] = res.reason
                return out

        issue = await client.create_issue(
            queue=q,
            summary=summary,
            description=description or None,
            priority=priority or None,
            assignee=assignee_login,
            issue_type=resolved_type,
            tags=parse_tags(tags) or None,
            deadline=deadline_val,
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


@platform_tool(name="tracker_create_epic", risk="medium", scopes=["tracker:write"])
async def tracker_create_epic(
    summary: str,
    queue: str = "",
    description: str = "",
    priority: str = "",
    assignee: str = "",
    tags: str = "",
    deadline: str = "",
    followers: str = "",
    custom_fields: str = "",
) -> dict[str, Any]:
    """Create an epic in Yandex Tracker. Lead/admin only."""
    forbidden = _require_lead_or_admin("Epic creation")
    if forbidden is not None:
        return forbidden
    return await tracker_create_issue(
        summary=summary,
        queue=queue,
        description=description,
        priority=priority,
        assignee=assignee,
        issue_type="epic",
        tags=tags,
        deadline=deadline,
        followers=followers,
        custom_fields=custom_fields,
    )


@platform_tool(name="tracker_open_epic", risk="medium", scopes=["tracker:write"])
async def tracker_open_epic(issue_key: str, comment: str = "") -> dict[str, Any]:
    """Open/reopen an epic through the issue workflow. Lead/admin only."""
    forbidden = _require_lead_or_admin("Epic opening")
    if forbidden is not None:
        return forbidden
    return await tracker_transition_issue(issue_key, "open", comment=comment)


@platform_tool(name="tracker_close_epic", risk="high", scopes=["tracker:write"])
async def tracker_close_epic(
    issue_key: str,
    resolution: str = "fixed",
    comment: str = "",
) -> dict[str, Any]:
    """Close an epic through the issue workflow. Lead/admin only."""
    forbidden = _require_lead_or_admin("Epic closing")
    if forbidden is not None:
        return forbidden
    return await tracker_close_issue(issue_key, resolution=resolution, comment=comment)


@platform_tool(name="tracker_create_sprint", risk="medium", scopes=["tracker:write"])
async def tracker_create_sprint(
    name: str,
    start_date: str,
    end_date: str,
    board_id: str = "",
    board_name: str = "",
) -> dict[str, Any]:
    """
    Create a sprint on a Yandex Tracker board.

    board_id: numeric board ID from Tracker Agile board URL/API.
    board_name: board name; used when board_id is empty.
    start_date/end_date: YYYY-MM-DD.
    Use when the user asks to create/start planning a new sprint.
    """
    forbidden = _require_lead_or_admin("Sprint creation")
    if forbidden is not None:
        return forbidden
    board_id, board_name = _with_default_board(board_id, board_name)
    if not name.strip():
        return {"error": "name is required"}
    if not board_id.strip() and not board_name.strip():
        return {"error": "board_id or board_name is required"}
    if not start_date.strip() or not end_date.strip():
        return {"error": "start_date and end_date are required in YYYY-MM-DD format"}

    async with TrackerClient() as client:
        resolved_board_id, board_meta = await _resolve_board_id(
            client, board_id=board_id, board_name=board_name
        )
        sprint = await client.create_sprint(
            name=name.strip(),
            board_id=resolved_board_id,
            start_date=start_date.strip(),
            end_date=end_date.strip(),
        )

    board = sprint.get("board") or {}
    return {
        "id": sprint.get("id"),
        "name": sprint.get("name"),
        "status": sprint.get("status"),
        "archived": sprint.get("archived"),
        "board_id": board.get("id") or resolved_board_id,
        "board": board.get("display") or board_meta.get("board_name"),
        "start_date": sprint.get("startDate"),
        "end_date": sprint.get("endDate"),
        "url": sprint.get("self"),
        "raw": sprint,
    }


@platform_tool(name="tracker_add_issues_to_sprint", risk="medium", scopes=["tracker:write"])
async def tracker_add_issues_to_sprint(
    issue_keys: str,
    sprint_id: str = "",
    sprint_name: str = "",
    board_id: str = "",
    board_name: str = "",
    preserve_existing: bool = True,
) -> dict[str, Any]:
    """
    Add one or more Tracker issues to a sprint.

    issue_keys: comma-separated issue keys, e.g. DARKHORSE-1,DARKHORSE-2.
    Provide sprint_id, or sprint_name with board_id/board_name when names may repeat.
    """
    keys = [key.upper() for key in _split_csv(issue_keys)]
    if not keys:
        return {"error": "issue_keys is required (comma-separated)"}

    async with TrackerClient() as client:
        resolved_sprint_id, sprint_meta = await _resolve_sprint_id(
            client,
            sprint_id=sprint_id,
            sprint_name=sprint_name,
            board_id=board_id,
            board_name=board_name,
        )
        updated: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for key in keys:
            try:
                issue = await client.add_issue_to_sprint(
                    key,
                    resolved_sprint_id,
                    preserve_existing=preserve_existing,
                )
                updated.append(issue_summary(issue, detailed=True))
            except TrackerError as exc:
                errors.append({"issue_key": key, "error": str(exc)})

    return {
        "sprint_id": resolved_sprint_id,
        **sprint_meta,
        "updated_count": len(updated),
        "error_count": len(errors),
        "issues": updated,
        "errors": errors,
    }


@platform_tool(name="tracker_open_sprint", risk="medium", scopes=["tracker:write"])
async def tracker_open_sprint(
    sprint_id: str = "",
    sprint_name: str = "",
    board_id: str = "",
    board_name: str = "",
) -> dict[str, Any]:
    """Open/unarchive a Yandex Tracker sprint. Lead/admin only."""
    forbidden = _require_lead_or_admin("Sprint opening")
    if forbidden is not None:
        return forbidden
    board_id, board_name = _with_default_board(board_id, board_name)
    async with TrackerClient() as client:
        resolved_sprint_id, sprint_meta = await _resolve_sprint_id(
            client,
            sprint_id=sprint_id,
            sprint_name=sprint_name,
            board_id=board_id,
            board_name=board_name,
        )
        sprint = await client.open_sprint(resolved_sprint_id)
    return {
        **_sprint_summary(
            sprint,
            fallback_board_id=str(sprint_meta.get("board_id") or board_id),
            fallback_board_name=str(sprint_meta.get("board") or board_name),
        ),
        "opened": True,
    }


@platform_tool(name="tracker_close_sprint", risk="high", scopes=["tracker:write"])
async def tracker_close_sprint(
    sprint_id: str = "",
    sprint_name: str = "",
    board_id: str = "",
    board_name: str = "",
) -> dict[str, Any]:
    """Close/archive a Yandex Tracker sprint. Lead/admin only."""
    forbidden = _require_lead_or_admin("Sprint closing")
    if forbidden is not None:
        return forbidden
    board_id, board_name = _with_default_board(board_id, board_name)
    async with TrackerClient() as client:
        resolved_sprint_id, sprint_meta = await _resolve_sprint_id(
            client,
            sprint_id=sprint_id,
            sprint_name=sprint_name,
            board_id=board_id,
            board_name=board_name,
        )
        sprint = await client.close_sprint(resolved_sprint_id)
    return {
        **_sprint_summary(
            sprint,
            fallback_board_id=str(sprint_meta.get("board_id") or board_id),
            fallback_board_name=str(sprint_meta.get("board") or board_name),
        ),
        "closed": True,
    }


@platform_tool(name="tracker_rollover_sprint", risk="high", scopes=["tracker:write"])
async def tracker_rollover_sprint(
    sprint_id: str = "",
    sprint_name: str = "",
    board_id: str = "",
    board_name: str = "",
    next_name: str = "",
    queue: str = "",
) -> dict[str, Any]:
    """
    Close the current sprint, create the next one, and move non-closed issues.

    Issue workflow statuses are not changed; only the sprint field is patched.
    """
    forbidden = _require_lead_or_admin("Sprint rollover")
    if forbidden is not None:
        return forbidden
    board_id, board_name = _with_default_board(board_id, board_name)
    if not board_id.strip() and not board_name.strip():
        return {"error": "board_id or board_name is required"}

    q = _effective_queue(queue)
    async with TrackerClient() as client:
        resolved_board_id, board_meta = await _resolve_board_id(
            client, board_id=board_id, board_name=board_name
        )
        if sprint_id.strip() or sprint_name.strip():
            resolved_sprint_id, sprint_meta = await _resolve_sprint_id(
                client,
                sprint_id=sprint_id,
                sprint_name=sprint_name,
                board_id=resolved_board_id,
                board_name=board_name,
            )
            old_sprint = await client.get_sprint(resolved_sprint_id)
            old_sprint = {
                "board_id": sprint_meta.get("board_id") or resolved_board_id,
                **old_sprint,
            }
        else:
            sprints = await client.list_sprints(resolved_board_id)
            old_sprint = _select_current_sprint(sprints)
            if old_sprint is None:
                return {"error": "No open/current sprint found for the board"}

        old_id = str(old_sprint.get("id") or "")
        if not old_id:
            return {"error": "Current sprint has no id", "sprint": old_sprint}

        dates = _next_sprint_dates(old_sprint)
        if dates is None:
            return {"error": "Current sprint has invalid startDate/endDate", "sprint": old_sprint}
        next_start, next_end = dates
        created_sprint = await client.create_sprint(
            name=next_name.strip() or _next_sprint_name(str(old_sprint.get("name") or "")),
            board_id=resolved_board_id,
            start_date=next_start,
            end_date=next_end,
        )
        new_id = str(created_sprint.get("id") or "")
        if not new_id:
            return {"error": "Created sprint has no id", "new_sprint": created_sprint}

        all_issues = await client.search_all_issues(f'Queue: "{q}"', queue=q, page_size=200)
        candidates = [
            issue
            for issue in all_issues
            if _issue_has_sprint(issue, old_id) and not _is_closed_issue(issue)
        ]

        moved: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for issue in candidates:
            key = str(issue.get("key") or "")
            if not key:
                continue
            try:
                updated = await client.move_issue_to_sprint(key, old_id, new_id)
                moved.append(issue_summary(updated, detailed=True))
            except TrackerError as exc:
                errors.append({"issue_key": key, "error": str(exc)})

        closed_sprint: dict[str, Any] | None = None
        close_error: str | None = None
        try:
            closed_sprint = await client.close_sprint(old_id)
        except TrackerError as exc:
            close_error = str(exc)

    return {
        "old_sprint": _sprint_summary(
            old_sprint,
            fallback_board_id=resolved_board_id,
            fallback_board_name=str(board_meta.get("board_name") or board_name),
        ),
        "new_sprint": _sprint_summary(
            created_sprint,
            fallback_board_id=resolved_board_id,
            fallback_board_name=str(board_meta.get("board_name") or board_name),
        ),
        "closed_sprint": (
            _sprint_summary(
                closed_sprint,
                fallback_board_id=resolved_board_id,
                fallback_board_name=str(board_meta.get("board_name") or board_name),
            )
            if closed_sprint is not None
            else None
        ),
        "close_error": close_error,
        "moved_count": len(moved),
        "error_count": len(errors),
        "issues": moved,
        "errors": errors,
        "statuses_preserved": True,
    }


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
    if not issue_key or not issue_key.strip():
        return {"error": "issue_key is required (e.g. DARKHORSE-195)"}
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
    """Update basic fields.

    Prefer `tracker_patch_issue` when more fields are required.
    """
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


@platform_tool(name="tracker_move_issues_to_in_progress", risk="medium", scopes=["tracker:write"])
async def tracker_move_issues_to_in_progress(
    issue_keys: str,
    comment: str = "",
) -> dict[str, Any]:
    """
    Move one or more issues to the in-progress status ("В работе").

    issue_keys: comma-separated issue keys, e.g. DARKHORSE-1,DARKHORSE-2.
    """
    keys = [key.upper() for key in _split_csv(issue_keys)]
    if not keys:
        return {"error": "issue_keys is required (comma-separated)"}

    updated: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    async with TrackerClient() as client:
        for key in keys:
            try:
                result = await client.transition_issue(
                    key,
                    "in_progress",
                    comment=comment or None,
                )
                issue = await client.get_issue(key)
                updated.append(
                    {
                        "issue_key": key,
                        "transition": result,
                        "issue": issue_summary(issue, detailed=True),
                    }
                )
            except TrackerError as exc:
                errors.append({"issue_key": key, "error": str(exc)})

    return {
        "target_status": "in_progress",
        "updated_count": len(updated),
        "error_count": len(errors),
        "issues": updated,
        "errors": errors,
    }


@platform_tool(name="tracker_link_issues", risk="medium", scopes=["tracker:write"])
async def tracker_link_issues(issue_key: str, parent_key: str) -> dict[str, Any]:
    """Set parent issue (make issue_key a subtask of parent_key)."""
    async with TrackerClient() as client:
        issue = await client.set_parent(issue_key, parent_key)
    return issue_summary(issue, detailed=True)


@platform_tool(name="tracker_close_issues", risk="high", scopes=["tracker:write"])
async def tracker_close_issues(
    issue_keys: str,
    resolution: str = "fixed",
    comment: str = "",
) -> dict[str, Any]:
    """
    Close one or more Yandex Tracker issues.

    issue_keys: comma-separated issue keys, e.g. DARKHORSE-1,DARKHORSE-2.
    resolution: fixed, wontFix, duplicate, etc.
    """
    keys = [key.upper() for key in _split_csv(issue_keys)]
    if not keys:
        return {"error": "issue_keys is required (comma-separated)"}

    closed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    async with TrackerClient() as client:
        for key in keys:
            try:
                result = await client.transition_issue(
                    key,
                    "closed",
                    resolution=resolution or None,
                    comment=comment or None,
                )
                issue = await client.get_issue(key)
                closed.append(
                    {
                        "issue_key": key,
                        "transition": result,
                        "issue": issue_summary(issue, detailed=True),
                    }
                )
            except TrackerError as exc:
                errors.append({"issue_key": key, "error": str(exc)})

    return {
        "target_status": "closed",
        "closed_count": len(closed),
        "error_count": len(errors),
        "issues": closed,
        "errors": errors,
    }


@platform_tool(name="tracker_comment_issue", risk="low", scopes=["tracker:write"])
async def tracker_comment_issue(issue_key: str, text: str) -> dict[str, Any]:
    """
    Add a comment to an issue — for new context from chat (blockers, problems, notes).
    Use when the user message contains info beyond field updates (VPN, bugs, «Имя: …»).
    """
    if not issue_key or not issue_key.strip():
        return {"error": "issue_key is required (e.g. DARKHORSE-195)"}
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
    if not issue_key or not issue_key.strip():
        return {"error": "issue_key is required (e.g. DARKHORSE-195)"}
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


def _today_iso() -> str:
    from datetime import date

    return date.today().isoformat()


def _is_terminal_status(status: str | None) -> bool:
    if not status:
        return False
    s = status.lower()
    return any(t in s for t in ("закры", "отмен", "closed", "cancel", "resolved", "решён", "решен"))


@platform_tool(name="tracker_board_snapshot", risk="low", scopes=["tracker:read"])
async def tracker_board_snapshot(
    queue: str = "",
    include_closed: bool = False,
    at_risk_days: int = 3,
) -> dict[str, Any]:
    """
    One aggregate read of the whole board: counts by status and assignee, plus
    lists of overdue / unassigned / no-estimate / no-deadline / at-risk issues,
    and story point sums per assignee (`by_assignee_sp`), per status
    (`by_status_sp`), and total (`total_sp`).

    Use for board digests, standup reports, proactive sweeps and hygiene checks —
    instead of many separate searches. Read-only, fully autonomous (low risk).
    """
    from datetime import date, timedelta

    q = _effective_queue(queue)
    query = f'Queue: "{q}"'
    if not include_closed:
        query += " AND Resolution: empty()"
    async with TrackerClient() as client:
        raw_issues = await client.search_all_issues(query, queue=q, page_size=200)

    today = date.today()
    risk_cutoff = today + timedelta(days=max(0, at_risk_days))
    by_status: dict[str, int] = {}
    by_assignee: dict[str, int] = {}
    overdue: list[dict[str, Any]] = []
    unassigned: list[dict[str, Any]] = []
    no_estimate: list[dict[str, Any]] = []
    no_deadline: list[dict[str, Any]] = []
    at_risk: list[dict[str, Any]] = []
    by_assignee_sp: dict[str, float] = {}
    by_status_sp: dict[str, float] = {}
    total_sp: float = 0.0

    for issue in raw_issues:
        summary = issue_summary(issue, detailed=False)
        status = summary.get("status")
        terminal = _is_terminal_status(status)
        if not include_closed and terminal:
            continue
        by_status[status or "—"] = by_status.get(status or "—", 0) + 1
        who = summary.get("assignee") or "(не назначен)"
        by_assignee[who] = by_assignee.get(who, 0) + 1

        sp = 0.0
        sp_raw = summary.get("story_points")
        if sp_raw not in (None, "", 0):
            try:
                sp = float(sp_raw)
            except (ValueError, TypeError):
                sp = 0.0
        who_sp = summary.get("assignee") or "(не назначен)"
        by_assignee_sp[who_sp] = by_assignee_sp.get(who_sp, 0.0) + sp
        by_status_sp[status or "—"] = by_status_sp.get(status or "—", 0.0) + sp
        total_sp += sp

        light = {
            "key": summary.get("key"),
            "summary": summary.get("summary"),
            "assignee": summary.get("assignee"),
            "status": status,
            "deadline": summary.get("deadline"),
        }
        if not summary.get("assignee"):
            unassigned.append(light)
        if summary.get("story_points") in (None, "", 0):
            no_estimate.append(light)
        deadline = summary.get("deadline")
        if not deadline:
            no_deadline.append(light)
        else:
            try:
                dl = date.fromisoformat(str(deadline)[:10])
                if not terminal and dl < today:
                    overdue.append(light)
                elif not terminal and today <= dl <= risk_cutoff:
                    at_risk.append(light)
            except ValueError:
                pass

    return {
        "queue": q,
        "total": sum(by_status.values()),
        "by_status": by_status,
        "by_assignee": by_assignee,
        "overdue": overdue,
        "unassigned": unassigned,
        "no_estimate": no_estimate,
        "no_deadline": no_deadline,
        "at_risk": at_risk,
        "by_assignee_sp": by_assignee_sp,
        "by_status_sp": by_status_sp,
        "total_sp": total_sp,
        "as_of": _today_iso(),
    }


@platform_tool(name="tracker_read_comments", risk="low", scopes=["tracker:read"])
async def tracker_read_comments(issue_key: str, limit: int = 20) -> dict[str, Any]:
    """
    Read an issue's comment thread (newest last). Use to avoid re-posting a status
    already present, to correlate updates, or to detect stale tasks by last activity.
    """
    async with TrackerClient() as client:
        comments = await client.list_comments(issue_key, per_page=max(1, limit))
    items = [
        {
            "author": (c.get("createdBy") or {}).get("display")
            or (c.get("createdBy") or {}).get("id"),
            "created": c.get("createdAt"),
            "text": c.get("text", ""),
        }
        for c in comments[-limit:]
    ]
    return {"issue_key": issue_key, "count": len(items), "comments": items}


__all__ = [
    "tracker_get_queue_meta",
    "tracker_list_team_members",
    "tracker_resolve_assignee",
    "tracker_get_issue",
    "tracker_find_issues",
    "tracker_search_issues",
    "tracker_list_transitions",
    "tracker_board_snapshot",
    "tracker_read_comments",
    "tracker_create_epic",
    "tracker_open_epic",
    "tracker_close_epic",
    "tracker_create_issue",
    "tracker_create_sprint",
    "tracker_open_sprint",
    "tracker_close_sprint",
    "tracker_rollover_sprint",
    "tracker_add_issues_to_sprint",
    "tracker_patch_issue",
    "tracker_update_issue",
    "tracker_update_followers",
    "tracker_transition_issue",
    "tracker_move_issues_to_in_progress",
    "tracker_link_issues",
    "tracker_close_issues",
    "tracker_comment_issue",
    "tracker_close_issue",
]
