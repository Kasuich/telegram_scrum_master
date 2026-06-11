"""Board/personal metrics computed from Yandex Tracker issue dicts.

Pure functions over the raw issue payloads returned by :class:`core.tracker.TrackerClient`
so they can be unit-tested without a live Tracker. Used by the console-api to power
the user board, personal stats and (later) team health dashboards.
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

Issue = dict[str, Any]


def parse_dt(value: Any) -> datetime | None:
    """Parse a Tracker datetime/date string into an aware UTC datetime."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    # Tracker emits e.g. "2026-06-01T12:00:00.000+0000" or "2026-06-20".
    if text.endswith("Z"):
        text = text[:-1] + "+0000"
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def status_display(issue: Issue) -> str:
    status = issue.get("status")
    if isinstance(status, dict):
        return str(status.get("display") or status.get("key") or "—")
    return "—"


def status_key(issue: Issue) -> str:
    status = issue.get("status")
    if isinstance(status, dict):
        return str(status.get("key") or status.get("display") or "")
    return ""


def is_in_progress(issue: Issue) -> bool:
    key = status_key(issue).lower()
    display = status_display(issue).lower()
    return "progress" in key or "работ" in display


def is_resolved(issue: Issue) -> bool:
    if issue.get("resolvedAt"):
        return True
    status = issue.get("status")
    if isinstance(status, dict):
        stype = status.get("type")
        if isinstance(stype, dict) and stype.get("value") == "done":
            return True
    return False


def is_overdue(issue: Issue, *, now: datetime) -> bool:
    if is_resolved(issue):
        return False
    deadline = parse_dt(issue.get("deadline"))
    return deadline is not None and deadline < now


def lead_time_days(issue: Issue) -> float | None:
    created = parse_dt(issue.get("createdAt"))
    resolved = parse_dt(issue.get("resolvedAt"))
    if created is None or resolved is None:
        return None
    return max(0.0, (resolved - created).total_seconds() / 86400.0)


def throughput_series(
    resolved_issues: list[Issue], *, window_days: int, now: datetime
) -> list[dict[str, Any]]:
    """Daily count of resolved issues over the trailing ``window_days``."""
    start = (now - timedelta(days=window_days - 1)).date()
    buckets: dict[str, int] = {}
    for offset in range(window_days):
        day = start + timedelta(days=offset)
        buckets[day.isoformat()] = 0
    for issue in resolved_issues:
        resolved = parse_dt(issue.get("resolvedAt"))
        if resolved is None:
            continue
        key = resolved.date().isoformat()
        if key in buckets:
            buckets[key] += 1
    return [{"date": day, "closed": count} for day, count in sorted(buckets.items())]


def status_distribution(open_issues: list[Issue]) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter(status_display(issue) for issue in open_issues)
    return [{"status": status, "count": count} for status, count in counter.most_common()]


def personal_stats(
    open_issues: list[Issue],
    resolved_issues: list[Issue],
    *,
    window_days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate personal performance metrics for a single assignee."""
    now = now or datetime.now(timezone.utc)
    lead_times = [lt for issue in resolved_issues if (lt := lead_time_days(issue)) is not None]
    overdue = sum(1 for issue in open_issues if is_overdue(issue, now=now))
    in_progress = sum(1 for issue in open_issues if is_in_progress(issue))

    return {
        "window_days": window_days,
        "counts": {
            "assigned": len(open_issues),
            "in_progress": in_progress,
            "resolved": len(resolved_issues),
            "overdue": overdue,
        },
        "throughput": throughput_series(resolved_issues, window_days=window_days, now=now),
        "status_distribution": status_distribution(open_issues),
        "lead_time": {
            "count": len(lead_times),
            "avg_days": round(statistics.fmean(lead_times), 1) if lead_times else None,
            "median_days": round(statistics.median(lead_times), 1) if lead_times else None,
        },
    }


# Health sub-score weights (MVP defaults; sum to 1.0). See docs/UI_UPGRADE_PLAN.md §4.
HEALTH_WEIGHTS = {
    "timeliness": 0.35,  # not overdue
    "flow": 0.35,        # closing backlog rather than letting it grow
    "hygiene": 0.30,     # open work has a planned deadline
}

_SUBSCORE_LABEL = {
    "timeliness": "Сроки",
    "flow": "Поток",
    "hygiene": "Гигиена доски",
}


def _has_deadline(issue: Issue) -> bool:
    return parse_dt(issue.get("deadline")) is not None


def team_health(
    members: list[dict[str, Any]],
    *,
    window_days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate team metrics and a 0..100 health index.

    Each member dict: ``{"user_id", "display_name", "tracker_login",
    "open": [issue...], "resolved": [issue...]}``.
    """
    now = now or datetime.now(timezone.utc)

    all_open: list[Issue] = []
    all_resolved: list[Issue] = []
    member_rows: list[dict[str, Any]] = []
    for member in members:
        open_issues = member.get("open") or []
        resolved_issues = member.get("resolved") or []
        all_open.extend(open_issues)
        all_resolved.extend(resolved_issues)
        overdue = sum(1 for issue in open_issues if is_overdue(issue, now=now))
        member_rows.append(
            {
                "user_id": member.get("user_id"),
                "display_name": member.get("display_name"),
                "tracker_login": member.get("tracker_login"),
                "assigned": len(open_issues),
                "in_progress": sum(1 for issue in open_issues if is_in_progress(issue)),
                "resolved": len(resolved_issues),
                "overdue": overdue,
            }
        )

    total_open = len(all_open)
    total_resolved = len(all_resolved)
    total_overdue = sum(1 for issue in all_open if is_overdue(issue, now=now))
    with_deadline = sum(1 for issue in all_open if _has_deadline(issue))

    # Sub-scores, each 0..100 (higher is better).
    # timeliness: share of open work that is NOT overdue.
    timeliness = 100.0 * (1 - total_overdue / total_open) if total_open else 100.0
    # flow: are we closing at least as much as currently sits open? (backlog is normal)
    flow = 100.0 * min(1.0, total_resolved / total_open) if total_open else 100.0
    # hygiene: share of open work that has a planned deadline.
    hygiene = 100.0 * (with_deadline / total_open) if total_open else 100.0
    subscores = {"timeliness": timeliness, "flow": flow, "hygiene": hygiene}

    index = round(sum(HEALTH_WEIGHTS[key] * score for key, score in subscores.items()))
    breakdown = [
        {
            "key": key,
            "label": _SUBSCORE_LABEL[key],
            "score": round(score),
            "weight": HEALTH_WEIGHTS[key],
        }
        for key, score in subscores.items()
    ]
    # "What drags it down" — lowest sub-scores first.
    drags = [
        item["label"]
        for item in sorted(breakdown, key=lambda i: i["score"])
        if item["score"] < 70
    ]

    return {
        "window_days": window_days,
        "health_index": index,
        "breakdown": breakdown,
        "drags": drags[:3],
        "totals": {
            "members": len(members),
            "open": total_open,
            "in_progress": sum(1 for issue in all_open if is_in_progress(issue)),
            "resolved": total_resolved,
            "overdue": total_overdue,
        },
        "throughput": throughput_series(all_resolved, window_days=window_days, now=now),
        "members": sorted(member_rows, key=lambda r: (-r["overdue"], -r["assigned"])),
    }
