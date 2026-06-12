from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.audit import build_audit_digest

NOW = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _date(days_from_now: int) -> str:
    return (NOW + timedelta(days=days_from_now)).date().isoformat()


def _issue(key, *, assignee=None, status="open", deadline=None, sp=None, created=10, updated=1):
    issue = {
        "key": key,
        "summary": f"Task {key}",
        "status": {"key": status, "display": status},
        "createdAt": _iso(created),
        "updatedAt": _iso(updated),
    }
    if assignee is not None:
        issue["assignee"] = {"id": assignee, "display": assignee.title()}
    if deadline is not None:
        issue["deadline"] = deadline
    if sp is not None:
        issue["storyPoints"] = sp
    return issue


def _resolved(key, *, assignee, created=10, resolved=2):
    return {
        "key": key,
        "summary": f"Done {key}",
        "status": {"key": "closed", "type": {"value": "done"}},
        "assignee": {"id": assignee, "display": assignee.title()},
        "createdAt": _iso(created),
        "resolvedAt": _iso(resolved),
    }


def test_build_audit_digest_full():
    open_issues = [
        # alice: 1 overdue (no deadline-future), 1 healthy
        _issue("Q-1", assignee="alice", deadline=_date(-2), sp=3),
        _issue("Q-2", assignee="alice", status="inProgress", deadline=_date(5), sp=2),
        # bob: stale (no update 10 days) + no deadline + no estimate
        _issue("Q-3", assignee="bob", updated=10, created=40),
        # unassigned, no estimate, no deadline
        _issue("Q-4", deadline=None, sp=None),
    ]
    resolved_issues = [
        _resolved("Q-9", assignee="alice", created=8, resolved=2),
        _resolved("Q-10", assignee="bob", created=12, resolved=3),
    ]

    digest = build_audit_digest(
        open_issues, resolved_issues, queue="Q", window_days=14, now=NOW
    )

    assert digest["queue"] == "Q"
    t = digest["totals"]
    assert t["open"] == 4
    assert t["resolved_window"] == 2
    assert t["overdue"] == 1  # Q-1
    assert t["unassigned"] == 1  # Q-4
    assert t["no_deadline"] >= 2  # Q-3, Q-4
    assert t["stale"] == 1  # Q-3 (idle 10d) and aging (40d)
    assert t["people"] == 2

    assert 0 <= digest["health_index"] <= 100
    assert {b["key"] for b in digest["health_breakdown"]} == {"timeliness", "flow", "hygiene"}

    # overdue sample carries the right key
    overdue_keys = {p["key"] for p in digest["problems"]["overdue"]}
    assert overdue_keys == {"Q-1"}

    people = {p["tracker_login"]: p for p in digest["people"]}
    assert set(people) == {"alice", "bob"}
    # alice sorted first: she has the overdue task
    assert digest["people"][0]["tracker_login"] == "alice"
    assert people["alice"]["counts"]["overdue"] == 1
    assert people["bob"]["counts"]["stale"] == 1
    assert people["bob"]["oldest_open"]["key"] == "Q-3"
    # load share sums to ~ assigned/total_open (3 assigned of 4 open)
    assert abs(sum(p["load_share"] for p in digest["people"]) - 0.75) < 0.01


def test_empty_board():
    digest = build_audit_digest([], [], queue="Q", window_days=14, now=NOW)
    assert digest["totals"]["open"] == 0
    assert digest["totals"]["people"] == 0
    assert digest["people"] == []
    assert digest["health_index"] == 100
