"""Board audit digest — a thorough, PM-oriented snapshot of a Tracker board.

Two layers:

* :func:`gather_board_issues` — the only side-effectful part; pulls open and
  recently-resolved issues for a queue via :class:`core.tracker.TrackerClient`.
* :func:`build_audit_digest` — a *pure* function over raw issue payloads that
  computes everything a project manager looks at: board health, hygiene gaps
  (no deadline / no estimate / unassigned), overdue and stale work, flow, and a
  per-person breakdown with load balance and aging.

The pure split mirrors :mod:`core.board_metrics` so the heavy logic is unit
testable without a live Tracker. The digest is consumed by the ``audit_agent``
(via the ``audit_board_digest`` tool), which turns it into a Russian report.
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from core import board_metrics
from core.board_metrics import (
    Issue,
    is_in_progress,
    is_overdue,
    is_resolved,
    lead_time_days,
    parse_dt,
    status_display,
)

# A task that has not been touched in this many days is "stale" — likely stuck.
STALE_DAYS = 7
# An open task older than this (since creation) is "aging" — long cycle time.
AGING_DAYS = 30
# How many example issue keys to attach per problem bucket (keep the digest small).
SAMPLE_LIMIT = 8


def assignee_login(issue: Issue) -> str | None:
    """Tracker login of the assignee, or ``None`` when unassigned."""
    who = issue.get("assignee")
    if isinstance(who, dict):
        return str(who.get("id") or who.get("login") or who.get("display") or "") or None
    if isinstance(who, str) and who.strip():
        return who.strip()
    return None


def assignee_name(issue: Issue) -> str | None:
    """Human-readable assignee name, falling back to the login."""
    who = issue.get("assignee")
    if isinstance(who, dict):
        return str(who.get("display") or who.get("id") or "") or None
    return assignee_login(issue)


def _story_points(issue: Issue) -> float:
    raw = issue.get("storyPoints")
    if raw in (None, "", 0):
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _has_estimate(issue: Issue) -> bool:
    return issue.get("storyPoints") not in (None, "", 0)


def _has_deadline(issue: Issue) -> bool:
    return parse_dt(issue.get("deadline")) is not None


def _age_days(issue: Issue, field: str, *, now: datetime) -> float | None:
    dt = parse_dt(issue.get(field))
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def _is_stale(issue: Issue, *, now: datetime) -> bool:
    """Open task with no activity (update) for ``STALE_DAYS`` days."""
    if is_resolved(issue):
        return False
    age = _age_days(issue, "updatedAt", now=now)
    return age is not None and age >= STALE_DAYS


def _is_aging(issue: Issue, *, now: datetime) -> bool:
    """Open task that has been open (since creation) longer than ``AGING_DAYS``."""
    if is_resolved(issue):
        return False
    age = _age_days(issue, "createdAt", now=now)
    return age is not None and age >= AGING_DAYS


def _light(issue: Issue, *, now: datetime) -> dict[str, Any]:
    """Compact issue card for problem samples."""
    return {
        "key": issue.get("key"),
        "summary": issue.get("summary"),
        "assignee": assignee_name(issue),
        "status": status_display(issue),
        "deadline": (issue.get("deadline") or None),
        "age_days": _round(_age_days(issue, "createdAt", now=now)),
        "idle_days": _round(_age_days(issue, "updatedAt", now=now)),
    }


def _round(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


def _samples(issues: list[Issue], *, now: datetime) -> list[dict[str, Any]]:
    return [_light(issue, now=now) for issue in issues[:SAMPLE_LIMIT]]


def _person_digest(
    login: str,
    name: str,
    open_issues: list[Issue],
    resolved_issues: list[Issue],
    *,
    total_open: int,
    now: datetime,
) -> dict[str, Any]:
    overdue = [i for i in open_issues if is_overdue(i, now=now)]
    no_deadline = [i for i in open_issues if not _has_deadline(i)]
    no_estimate = [i for i in open_issues if not _has_estimate(i)]
    stale = [i for i in open_issues if _is_stale(i, now=now)]
    aging = [i for i in open_issues if _is_aging(i, now=now)]
    in_progress = [i for i in open_issues if is_in_progress(i)]
    lead_times = [lt for i in resolved_issues if (lt := lead_time_days(i)) is not None]

    oldest = None
    if open_issues:
        oldest_issue = max(
            open_issues,
            key=lambda i: _age_days(i, "createdAt", now=now) or 0.0,
        )
        oldest = _light(oldest_issue, now=now)

    return {
        "tracker_login": login,
        "display_name": name,
        "counts": {
            "assigned": len(open_issues),
            "in_progress": len(in_progress),
            "resolved": len(resolved_issues),
            "overdue": len(overdue),
            "no_deadline": len(no_deadline),
            "no_estimate": len(no_estimate),
            "stale": len(stale),
            "aging": len(aging),
        },
        # Share of the whole board's open WIP carried by this person (0..1).
        "load_share": round(len(open_issues) / total_open, 2) if total_open else 0.0,
        "story_points": round(sum(_story_points(i) for i in open_issues), 1),
        "lead_time_avg_days": _round(statistics.fmean(lead_times)) if lead_times else None,
        "oldest_open": oldest,
        "samples": {
            "overdue": _samples(overdue, now=now),
            "stale": _samples(stale, now=now),
            "no_deadline": _samples(no_deadline, now=now),
        },
    }


def build_audit_digest(
    open_issues: list[Issue],
    resolved_issues: list[Issue],
    *,
    queue: str,
    window_days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Pure aggregation of board health, hygiene and per-person breakdown.

    ``open_issues`` are unresolved issues on the board; ``resolved_issues`` are
    those closed within the trailing ``window_days``. Returns a JSON-serializable
    digest ready to be handed to the audit LLM agent.
    """
    now = now or datetime.now(timezone.utc)
    total_open = len(open_issues)

    # Group by assignee (login). Unassigned open work is tracked separately.
    by_login_open: dict[str, list[Issue]] = {}
    by_login_resolved: dict[str, list[Issue]] = {}
    names: dict[str, str] = {}
    unassigned: list[Issue] = []
    for issue in open_issues:
        login = assignee_login(issue)
        if login is None:
            unassigned.append(issue)
            continue
        by_login_open.setdefault(login, []).append(issue)
        names.setdefault(login, assignee_name(issue) or login)
    for issue in resolved_issues:
        login = assignee_login(issue)
        if login is None:
            continue
        by_login_resolved.setdefault(login, []).append(issue)
        names.setdefault(login, assignee_name(issue) or login)

    people = [
        _person_digest(
            login,
            names.get(login, login),
            by_login_open.get(login, []),
            by_login_resolved.get(login, []),
            total_open=total_open,
            now=now,
        )
        for login in sorted(
            set(by_login_open) | set(by_login_resolved),
            key=lambda lg: (
                -sum(1 for i in by_login_open.get(lg, []) if is_overdue(i, now=now)),
                -len(by_login_open.get(lg, [])),
            ),
        )
    ]

    # Reuse the shared team-health index (Сроки / Поток / Гигиена, 0..100).
    members = [
        {
            "user_id": login,
            "display_name": names.get(login, login),
            "tracker_login": login,
            "open": by_login_open.get(login, []),
            "resolved": by_login_resolved.get(login, []),
        }
        for login in set(by_login_open) | set(by_login_resolved)
    ]
    health = board_metrics.team_health(members, window_days=window_days, now=now)

    overdue = [i for i in open_issues if is_overdue(i, now=now)]
    no_deadline = [i for i in open_issues if not _has_deadline(i)]
    no_estimate = [i for i in open_issues if not _has_estimate(i)]
    stale = [i for i in open_issues if _is_stale(i, now=now)]
    aging = [i for i in open_issues if _is_aging(i, now=now)]
    status_counter: Counter[str] = Counter(status_display(i) for i in open_issues)

    return {
        "queue": queue,
        "window_days": window_days,
        "as_of": now.date().isoformat(),
        "health_index": health["health_index"],
        "health_breakdown": health["breakdown"],
        "health_drags": health["drags"],
        "totals": {
            "open": total_open,
            "resolved_window": len(resolved_issues),
            "in_progress": sum(1 for i in open_issues if is_in_progress(i)),
            "overdue": len(overdue),
            "unassigned": len(unassigned),
            "no_deadline": len(no_deadline),
            "no_estimate": len(no_estimate),
            "stale": len(stale),
            "aging": len(aging),
            "people": len(people),
            "story_points_open": round(sum(_story_points(i) for i in open_issues), 1),
        },
        "status_distribution": [
            {"status": status, "count": count}
            for status, count in status_counter.most_common()
        ],
        "throughput": health["throughput"],
        "problems": {
            "overdue": _samples(overdue, now=now),
            "unassigned": _samples(unassigned, now=now),
            "no_deadline": _samples(no_deadline, now=now),
            "no_estimate": _samples(no_estimate, now=now),
            "stale": _samples(stale, now=now),
            "aging": _samples(aging, now=now),
        },
        "people": people,
    }


async def gather_board_issues(
    queue: str,
    *,
    window_days: int,
) -> tuple[list[Issue], list[Issue]]:
    """Fetch (open, resolved-within-window) issues for a queue from Tracker."""
    from datetime import timedelta

    from core.tracker import TrackerClient

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=window_days)).date().isoformat()
    async with TrackerClient() as client:
        open_issues = await client.search_all_issues(
            "Resolution: empty()", queue=queue, page_size=200
        )
        resolved_issues = await client.search_all_issues(
            f'Resolved: >= "{since}"', queue=queue, page_size=200
        )
    return open_issues, resolved_issues
