from __future__ import annotations

from datetime import datetime, timezone

from core.board_metrics import (
    is_in_progress,
    is_overdue,
    is_resolved,
    lead_time_days,
    parse_dt,
    personal_stats,
    status_distribution,
    team_health,
    throughput_series,
)

NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


def _issue(**fields):
    return fields


def test_parse_dt_handles_tracker_formats():
    assert parse_dt("2026-06-01T12:00:00.000+0000") == datetime(
        2026, 6, 1, 12, 0, tzinfo=timezone.utc
    )
    assert parse_dt("2026-06-20").date().isoformat() == "2026-06-20"
    assert parse_dt("2026-06-20T10:00:00Z").hour == 10
    assert parse_dt(None) is None
    assert parse_dt("garbage") is None


def test_status_helpers():
    issue = _issue(status={"key": "inProgress", "display": "В работе"})
    assert is_in_progress(issue)
    assert not is_resolved(issue)
    done = _issue(status={"key": "closed", "type": {"value": "done"}})
    assert is_resolved(done)
    assert is_resolved(_issue(resolvedAt="2026-06-10T10:00:00.000+0000"))


def test_overdue_only_counts_unresolved_past_deadline():
    overdue = _issue(deadline="2026-06-01", status={"key": "open"})
    assert is_overdue(overdue, now=NOW)
    future = _issue(deadline="2026-06-30", status={"key": "open"})
    assert not is_overdue(future, now=NOW)
    resolved = _issue(deadline="2026-06-01", resolvedAt="2026-06-02T00:00:00.000+0000")
    assert not is_overdue(resolved, now=NOW)


def test_lead_time_days():
    issue = _issue(
        createdAt="2026-06-01T00:00:00.000+0000",
        resolvedAt="2026-06-04T00:00:00.000+0000",
    )
    assert lead_time_days(issue) == 3.0
    assert lead_time_days(_issue(createdAt="2026-06-01")) is None


def test_throughput_series_buckets_by_day():
    resolved = [
        _issue(resolvedAt="2026-06-10T09:00:00.000+0000"),
        _issue(resolvedAt="2026-06-10T18:00:00.000+0000"),
        _issue(resolvedAt="2026-06-11T08:00:00.000+0000"),
    ]
    series = throughput_series(resolved, window_days=7, now=NOW)
    assert len(series) == 7
    by_day = {row["date"]: row["closed"] for row in series}
    assert by_day["2026-06-10"] == 2
    assert by_day["2026-06-11"] == 1
    assert by_day["2026-06-05"] == 0


def test_status_distribution_sorted_desc():
    issues = [
        _issue(status={"display": "Открыта"}),
        _issue(status={"display": "Открыта"}),
        _issue(status={"display": "В работе"}),
    ]
    dist = status_distribution(issues)
    assert dist[0] == {"status": "Открыта", "count": 2}


def test_personal_stats_aggregate():
    open_issues = [
        _issue(status={"key": "inProgress", "display": "В работе"}),
        _issue(status={"key": "open", "display": "Открыта"}, deadline="2026-06-01"),
    ]
    resolved_issues = [
        _issue(
            createdAt="2026-06-08T00:00:00.000+0000",
            resolvedAt="2026-06-10T00:00:00.000+0000",
        ),
    ]
    stats = personal_stats(open_issues, resolved_issues, window_days=14, now=NOW)
    assert stats["counts"] == {
        "assigned": 2,
        "in_progress": 1,
        "resolved": 1,
        "overdue": 1,
    }
    assert stats["lead_time"]["median_days"] == 2.0
    assert len(stats["throughput"]) == 14


def test_team_health_perfect_team():
    members = [
        {
            "user_id": "u1",
            "display_name": "Алиса",
            "tracker_login": "alice",
            "open": [_issue(status={"display": "Открыта"}, deadline="2026-06-30")],
            "resolved": [_issue(resolvedAt="2026-06-10T00:00:00.000+0000")],
        },
    ]
    health = team_health(members, window_days=14, now=NOW)
    assert health["health_index"] == 100
    assert health["totals"] == {
        "members": 1,
        "open": 1,
        "in_progress": 0,
        "resolved": 1,
        "overdue": 0,
    }
    assert health["drags"] == []


def test_team_health_penalises_overdue_and_no_deadline():
    members = [
        {
            "user_id": "u1",
            "display_name": "Боб",
            "tracker_login": "bob",
            # Overdue + no deadline coverage, no closures -> low health.
            "open": [
                _issue(status={"display": "Открыта"}, deadline="2026-06-01"),
                _issue(status={"display": "Открыта"}),
            ],
            "resolved": [],
        },
    ]
    health = team_health(members, window_days=14, now=NOW)
    assert health["health_index"] < 50
    assert health["members"][0]["overdue"] == 1
    assert "Сроки" in health["drags"]


def test_team_health_empty_team_is_neutral_full():
    health = team_health([], window_days=7, now=NOW)
    assert health["health_index"] == 100
    assert health["totals"]["members"] == 0
