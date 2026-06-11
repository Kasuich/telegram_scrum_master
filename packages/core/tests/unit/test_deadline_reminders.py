"""Unit tests for core.deadline_reminders."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from core.deadline_reminders import (
    DEADLINE_REMINDER_CATEGORY,
    DEADLINE_REMINDER_JOB_NAME,
    DEADLINE_REMINDER_PAYLOAD_TYPE,
    DeadlineIssue,
    ReminderRecipient,
    _resolve_lead,
    build_deadline_issues_yql,
    ensure_deadline_reminder_scheduled_job,
    fetch_member_deadline_issues,
    format_lead_summary,
    format_member_reminder,
    send_team_deadline_reminders,
)
from core.models import ScheduledJob, TelegramOutbox
from core.scheduler import SchedulerDaemon

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _recipient(
    *,
    login: str = "alice",
    display: str = "Alice",
    role: str = "user",
    external_user_id: str = "111",
    telegram_user_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
) -> ReminderRecipient:
    return ReminderRecipient(
        team_id=team_id or uuid.uuid4(),
        installation_id=uuid.uuid4(),
        telegram_user_id=telegram_user_id or uuid.uuid4(),
        external_user_id=external_user_id,
        user_id=uuid.uuid4(),
        tracker_login=login,
        display=display,
        membership_role=role,
    )


def _issue(
    *,
    key: str = "TEST-1",
    deadline: str = "2026-06-10",
    bucket: str = "soon",
    summary: str = "Fix bug",
) -> DeadlineIssue:
    return DeadlineIssue(
        key=key,
        summary=summary,
        deadline=deadline,
        status="Open",
        url=f"https://tracker.yandex.ru/{key}",
        bucket=bucket,
        assignee_login="alice",
        assignee_display="Alice",
    )


def _cfg(
    *,
    enabled: bool = True,
    cron: str = "0 * * * *",
    soon_days: int = 3,
    notify_assignees: bool = True,
    notify_lead: bool = True,
    lead_roles: str = "lead,admin",
    lead_login: str = "nukolaus",
) -> SimpleNamespace:
    reminder = SimpleNamespace(
        enabled=enabled,
        cron_expr=cron,
        timezone="Europe/Moscow",
        soon_days=soon_days,
        max_issues_per_member=20,
        notify_assignees=notify_assignees,
        notify_lead=notify_lead,
        lead_roles=lead_roles,
        lead_login=lead_login,
        lead_role_list=lambda: [p.strip() for p in lead_roles.split(",") if p.strip()],
    )
    return SimpleNamespace(
        deadline_reminder=reminder,
        tracker=SimpleNamespace(tracker_queue="TEST"),
    )


# ---------------------------------------------------------------------------
# FakeSession
# ---------------------------------------------------------------------------


class FakeSession:
    """Minimal session stub for deadline reminder tests."""

    def __init__(
        self,
        *,
        team_queue: str = "TEST",
        existing_outbox: TelegramOutbox | None = None,
        existing_job: ScheduledJob | None = None,
        agent_instance: Any = None,
    ) -> None:
        self.team_queue = team_queue
        self.existing_outbox = existing_outbox
        self.existing_job = existing_job
        self._agent_instance = agent_instance or SimpleNamespace(id=uuid.uuid4())
        self.added: list[Any] = []
        self.flushed = False
        self._execute_queue: list[Any] = []

    def _push_execute(self, value: Any) -> None:
        self._execute_queue.append(value)

    async def get(self, model: Any, key: Any) -> Any:
        del model, key
        return SimpleNamespace(tracker_queue=self.team_queue)

    async def execute(self, stmt: Any) -> Any:
        del stmt

        class _Result:
            def __init__(self, val: Any) -> None:
                self._val = val

            def scalar_one_or_none(self) -> Any:
                return self._val

            def scalars(self) -> _Result:
                return self

            def all(self) -> list[Any]:
                v = self._val
                return list(v) if isinstance(v, list) else ([v] if v is not None else [])

        if self._execute_queue:
            return _Result(self._execute_queue.pop(0))
        # default: outbox lookup returns None (no dupe), job lookup returns existing_job
        return _Result(self.existing_outbox or self.existing_job)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed = True


# ---------------------------------------------------------------------------
# FakeTrackerClient
# ---------------------------------------------------------------------------


class FakeTrackerClient:
    def __init__(self, issues_by_login: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.queries: list[str] = []
        self._issues = issues_by_login or {}

    async def __aenter__(self) -> FakeTrackerClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass

    async def search_issues(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        self.queries.append(query)
        for login, issues in self._issues.items():
            if f'Assignee: "{login}"' in query:
                return issues[:limit]
        return []


# ===========================================================================
# YQL builder
# ===========================================================================


class TestBuildDeadlineIssuesYql:
    def test_contains_queue(self) -> None:
        yql = build_deadline_issues_yql(
            queue="DARKHORSE", tracker_login="alice", cutoff_date=date(2026, 6, 14)
        )
        assert 'Queue: "DARKHORSE"' in yql

    def test_contains_assignee(self) -> None:
        yql = build_deadline_issues_yql(
            queue="TEST", tracker_login="bob", cutoff_date=date(2026, 6, 14)
        )
        assert 'Assignee: "bob"' in yql

    def test_contains_resolution_empty(self) -> None:
        yql = build_deadline_issues_yql(
            queue="TEST", tracker_login="alice", cutoff_date=date(2026, 6, 14)
        )
        assert "Resolution: empty()" in yql

    def test_contains_deadline_not_empty(self) -> None:
        yql = build_deadline_issues_yql(
            queue="TEST", tracker_login="alice", cutoff_date=date(2026, 6, 14)
        )
        assert "Deadline: notEmpty()" in yql

    def test_cutoff_is_iso_date_literal(self) -> None:
        yql = build_deadline_issues_yql(
            queue="TEST", tracker_login="alice", cutoff_date=date(2026, 6, 14)
        )
        assert 'Deadline: <= "2026-06-14"' in yql
        # no time component — pure date string
        assert "T00:00:00" not in yql

    def test_special_chars_escaped(self) -> None:
        yql = build_deadline_issues_yql(
            queue='T"EST', tracker_login='a"lice', cutoff_date=date(2026, 6, 14)
        )
        assert '\\"' in yql


# ===========================================================================
# Overdue vs soon classification
# ===========================================================================


class TestFetchMemberDeadlineIssues:
    async def test_overdue_when_deadline_before_today(self) -> None:
        client = FakeTrackerClient(
            issues_by_login={
                "alice": [
                    {
                        "key": "TEST-1",
                        "summary": "Old task",
                        "deadline": "2026-06-08",
                        "status": {"display": "Open"},
                        "assignee": {"login": "alice", "display": "Alice"},
                    }
                ]
            }
        )
        today = date(2026, 6, 11)
        overdue, soon = await fetch_member_deadline_issues(
            client,
            queue="TEST",
            tracker_login="alice",
            today=today,
            cutoff_date=today + __import__("datetime").timedelta(days=3),
            limit=20,
        )
        assert len(overdue) == 1
        assert overdue[0].key == "TEST-1"
        assert overdue[0].bucket == "overdue"
        assert soon == []

    async def test_soon_when_deadline_equals_today(self) -> None:
        client = FakeTrackerClient(
            issues_by_login={
                "alice": [
                    {
                        "key": "TEST-2",
                        "summary": "Due today",
                        "deadline": "2026-06-11",
                        "status": {"display": "Open"},
                        "assignee": {"login": "alice", "display": "Alice"},
                    }
                ]
            }
        )
        today = date(2026, 6, 11)
        overdue, soon = await fetch_member_deadline_issues(
            client,
            queue="TEST",
            tracker_login="alice",
            today=today,
            cutoff_date=today + __import__("datetime").timedelta(days=3),
            limit=20,
        )
        assert overdue == []
        assert len(soon) == 1
        assert soon[0].bucket == "soon"

    async def test_skips_issues_without_deadline_field(self) -> None:
        client = FakeTrackerClient(
            issues_by_login={
                "alice": [
                    {
                        "key": "TEST-3",
                        "summary": "No deadline",
                        "deadline": None,
                        "status": {},
                        "assignee": {},
                    }
                ]
            }
        )
        today = date(2026, 6, 11)
        overdue, soon = await fetch_member_deadline_issues(
            client,
            queue="TEST",
            tracker_login="alice",
            today=today,
            cutoff_date=today + __import__("datetime").timedelta(days=3),
            limit=20,
        )
        assert overdue == []
        assert soon == []

    async def test_deadline_iso_datetime_string_parsed(self) -> None:
        """Tracker sometimes returns deadline as YYYY-MM-DDTHH:MM:SS+TZ."""
        client = FakeTrackerClient(
            issues_by_login={
                "alice": [
                    {
                        "key": "TEST-4",
                        "summary": "ISO datetime",
                        "deadline": "2026-06-09T00:00:00+0000",
                        "status": {},
                        "assignee": {},
                    }
                ]
            }
        )
        today = date(2026, 6, 11)
        overdue, soon = await fetch_member_deadline_issues(
            client,
            queue="TEST",
            tracker_login="alice",
            today=today,
            cutoff_date=today + __import__("datetime").timedelta(days=3),
            limit=20,
        )
        assert len(overdue) == 1
        assert overdue[0].deadline == "2026-06-09"

    async def test_skips_issues_without_key(self) -> None:
        client = FakeTrackerClient(
            issues_by_login={
                "alice": [{"key": "", "deadline": "2026-06-10", "summary": "x", "status": {}}]
            }
        )
        today = date(2026, 6, 11)
        overdue, soon = await fetch_member_deadline_issues(
            client,
            queue="TEST",
            tracker_login="alice",
            today=today,
            cutoff_date=today + __import__("datetime").timedelta(days=3),
            limit=20,
        )
        assert overdue == []
        assert soon == []


# ===========================================================================
# format_member_reminder
# ===========================================================================


class TestFormatMemberReminder:
    def test_returns_none_when_no_issues(self) -> None:
        r = _recipient()
        assert (
            format_member_reminder(
                recipient=r,
                overdue=[],
                soon=[],
                local_date="2026-06-11",
            )
            is None
        )

    def test_overdue_section_present(self) -> None:
        r = _recipient(display="Ivan")
        text = format_member_reminder(
            recipient=r,
            overdue=[_issue(key="TEST-1", deadline="2026-06-09", bucket="overdue")],
            soon=[],
            local_date="2026-06-11",
        )
        assert text is not None
        assert "🔴" in text
        assert "TEST-1" in text
        assert "2026-06-09" in text

    def test_soon_section_present(self) -> None:
        r = _recipient(display="Ivan")
        text = format_member_reminder(
            recipient=r,
            overdue=[],
            soon=[_issue(key="TEST-2", deadline="2026-06-13", bucket="soon")],
            local_date="2026-06-11",
        )
        assert text is not None
        assert "🟡" in text
        assert "TEST-2" in text

    def test_both_sections_when_both_present(self) -> None:
        r = _recipient()
        text = format_member_reminder(
            recipient=r,
            overdue=[_issue(key="TEST-1", deadline="2026-06-09", bucket="overdue")],
            soon=[_issue(key="TEST-2", deadline="2026-06-13", bucket="soon")],
            local_date="2026-06-11",
        )
        assert text is not None
        assert "🔴" in text
        assert "🟡" in text

    def test_includes_tracker_url(self) -> None:
        r = _recipient()
        text = format_member_reminder(
            recipient=r,
            overdue=[_issue(key="TEST-42")],
            soon=[],
            local_date="2026-06-11",
        )
        assert text is not None
        assert "tracker.yandex.ru/TEST-42" in text

    def test_includes_local_date_in_header(self) -> None:
        r = _recipient(display="Sergey")
        text = format_member_reminder(
            recipient=r,
            overdue=[_issue()],
            soon=[],
            local_date="2026-06-11",
        )
        assert text is not None
        assert "2026-06-11" in text
        assert "Sergey" in text


# ===========================================================================
# format_lead_summary
# ===========================================================================


class TestFormatLeadSummary:
    def test_returns_none_when_all_empty(self) -> None:
        data = [
            (_recipient(login="alice"), [], []),
            (_recipient(login="bob"), [], []),
        ]
        assert format_lead_summary(member_data=data, local_date="2026-06-11") is None

    def test_only_members_with_issues_appear(self) -> None:
        data = [
            (_recipient(login="alice", display="Alice"), [_issue()], []),
            (_recipient(login="bob", display="Bob"), [], []),
        ]
        text = format_lead_summary(member_data=data, local_date="2026-06-11")
        assert text is not None
        assert "Alice" in text
        assert "Bob" not in text

    def test_overdue_and_soon_both_shown(self) -> None:
        overdue_issue = _issue(key="TEST-1", deadline="2026-06-09", bucket="overdue")
        soon_issue = _issue(key="TEST-2", deadline="2026-06-13", bucket="soon")
        data = [(_recipient(login="alice"), [overdue_issue], [soon_issue])]
        text = format_lead_summary(member_data=data, local_date="2026-06-11")
        assert text is not None
        assert "🔴" in text
        assert "🟡" in text
        assert "TEST-1" in text
        assert "TEST-2" in text

    def test_includes_date_in_header(self) -> None:
        data = [(_recipient(), [_issue()], [])]
        text = format_lead_summary(member_data=data, local_date="2026-06-11")
        assert text is not None
        assert "2026-06-11" in text


# ===========================================================================
# _resolve_lead
# ===========================================================================


class TestResolveLead:
    def test_resolves_by_role(self) -> None:
        recipients = [
            _recipient(login="alice", role="user"),
            _recipient(login="bob", role="lead"),
        ]
        lead = _resolve_lead(recipients, ["lead", "admin"], "fallback")
        assert lead is not None
        assert lead.tracker_login == "bob"

    def test_resolves_by_admin_role(self) -> None:
        recipients = [_recipient(login="admin_user", role="admin")]
        lead = _resolve_lead(recipients, ["lead", "admin"], "")
        assert lead is not None
        assert lead.tracker_login == "admin_user"

    def test_falls_back_to_login_when_no_role_match(self) -> None:
        recipients = [
            _recipient(login="nukolaus", role="user"),
            _recipient(login="other", role="user"),
        ]
        lead = _resolve_lead(recipients, ["lead"], "nukolaus")
        assert lead is not None
        assert lead.tracker_login == "nukolaus"

    def test_returns_none_when_no_match(self) -> None:
        recipients = [_recipient(login="alice", role="user")]
        lead = _resolve_lead(recipients, ["lead"], "nobody")
        assert lead is None

    def test_role_matching_is_case_insensitive(self) -> None:
        recipients = [_recipient(login="alice", role="Lead")]
        lead = _resolve_lead(recipients, ["lead"], "")
        assert lead is not None

    def test_login_matching_is_case_insensitive(self) -> None:
        recipients = [_recipient(login="NuKolAuS", role="user")]
        lead = _resolve_lead(recipients, ["lead"], "nukolaus")
        assert lead is not None


# ===========================================================================
# send_team_deadline_reminders
# ===========================================================================


class TestSendTeamDeadlineReminders:
    async def test_skipped_when_disabled(self) -> None:
        session = FakeSession()
        with patch("core.deadline_reminders.get_config", return_value=_cfg(enabled=False)):
            result = await send_team_deadline_reminders(session, team_id=uuid.uuid4())
        assert result["status"] == "skipped"
        assert result["reason"] == "disabled"

    async def test_skipped_when_no_participants(self) -> None:
        session = FakeSession()
        with (
            patch("core.deadline_reminders.get_config", return_value=_cfg()),
            patch(
                "core.deadline_reminders.load_reminder_recipients",
                AsyncMock(return_value=[]),
            ),
        ):
            result = await send_team_deadline_reminders(session, team_id=uuid.uuid4())
        assert result["status"] == "skipped"
        assert result["reason"] == "no_registered_participants"

    async def test_enqueues_private_outbox_rows(self) -> None:
        team_id = uuid.uuid4()
        alice = _recipient(login="alice", display="Alice", external_user_id="111")
        session = FakeSession()
        # Each call to execute → None (no existing outbox row)
        client = FakeTrackerClient(
            issues_by_login={
                "alice": [
                    {
                        "key": "TEST-1",
                        "summary": "Overdue task",
                        "deadline": "2026-06-09",
                        "status": {"display": "Open"},
                        "assignee": {"login": "alice", "display": "Alice"},
                    }
                ]
            }
        )
        now = datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc)

        with (
            patch("core.deadline_reminders.get_config", return_value=_cfg(notify_lead=False)),
            patch(
                "core.deadline_reminders.load_reminder_recipients",
                AsyncMock(return_value=[alice]),
            ),
        ):
            result = await send_team_deadline_reminders(
                session,
                team_id=team_id,
                now=now,
                client_factory=lambda: client,
            )

        assert result["status"] == "enqueued"
        # One assignee DM enqueued
        assert len(result["assignee_outbox_ids"]) == 1
        # The added object should be a TelegramOutbox with the right category
        outbox_rows = [o for o in session.added if isinstance(o, TelegramOutbox)]
        assert len(outbox_rows) == 1
        assert outbox_rows[0].category == DEADLINE_REMINDER_CATEGORY
        # Must be a private DM — target_user_id set
        assert outbox_rows[0].target_user_id == "111"
        assert session.flushed

    async def test_idempotent_within_same_hour_slot(self) -> None:
        """Running twice in the same hour slot must not create duplicate outbox rows."""
        team_id = uuid.uuid4()
        alice = _recipient(login="alice", external_user_id="111")
        client = FakeTrackerClient(
            issues_by_login={
                "alice": [
                    {
                        "key": "TEST-1",
                        "summary": "task",
                        "deadline": "2026-06-09",
                        "status": {},
                        "assignee": {},
                    }
                ]
            }
        )
        now = datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc)

        existing_outbox = TelegramOutbox(
            id=uuid.uuid4(),
            team_id=team_id,
            installation_id=alice.installation_id,
            category=DEADLINE_REMINDER_CATEGORY,
            target_user_id="111",
            target_chat_id="111",
            dedupe_key="x",
            priority=105,
            status="pending",
            attempts=0,
            payload={},
        )
        # Session always returns the existing row → no new row added
        session = FakeSession(existing_outbox=existing_outbox)

        with (
            patch("core.deadline_reminders.get_config", return_value=_cfg(notify_lead=False)),
            patch(
                "core.deadline_reminders.load_reminder_recipients",
                AsyncMock(return_value=[alice]),
            ),
        ):
            result = await send_team_deadline_reminders(
                session,
                team_id=team_id,
                now=now,
                client_factory=lambda: client,
            )

        assert result["status"] == "enqueued"
        new_outbox_rows = [o for o in session.added if isinstance(o, TelegramOutbox)]
        assert new_outbox_rows == []  # nothing new added — reused existing

    async def test_lead_summary_enqueued_for_lead_recipient(self) -> None:
        team_id = uuid.uuid4()
        lead = _recipient(login="nukolaus", display="Nikolay", role="lead", external_user_id="999")
        alice = _recipient(login="alice", display="Alice", role="user", external_user_id="111")
        client = FakeTrackerClient(
            issues_by_login={
                "alice": [
                    {
                        "key": "TEST-1",
                        "summary": "task",
                        "deadline": "2026-06-09",
                        "status": {},
                        "assignee": {"login": "alice", "display": "Alice"},
                    }
                ]
            }
        )
        now = datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc)
        session = FakeSession()

        with (
            patch("core.deadline_reminders.get_config", return_value=_cfg()),
            patch(
                "core.deadline_reminders.load_reminder_recipients",
                AsyncMock(return_value=[lead, alice]),
            ),
        ):
            result = await send_team_deadline_reminders(
                session,
                team_id=team_id,
                now=now,
                client_factory=lambda: client,
            )

        assert result["lead_outbox_id"] is not None
        outbox_rows = [o for o in session.added if isinstance(o, TelegramOutbox)]
        # One per-assignee DM (alice) + one lead summary (nukolaus)
        # Lead has no at-risk tasks itself — still receives summary
        assert len(outbox_rows) >= 1
        lead_dm = next(
            (o for o in outbox_rows if o.target_user_id == "999"),
            None,
        )
        assert lead_dm is not None

    async def test_no_lead_dm_when_no_at_risk_issues(self) -> None:
        team_id = uuid.uuid4()
        lead = _recipient(login="nukolaus", role="lead", external_user_id="999")
        client = FakeTrackerClient()  # returns empty for everyone
        now = datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc)
        session = FakeSession()

        with (
            patch("core.deadline_reminders.get_config", return_value=_cfg()),
            patch(
                "core.deadline_reminders.load_reminder_recipients",
                AsyncMock(return_value=[lead]),
            ),
        ):
            result = await send_team_deadline_reminders(
                session,
                team_id=team_id,
                now=now,
                client_factory=lambda: client,
            )

        # lead_outbox_id is None because format_lead_summary returns None when no member has issues
        assert result["lead_outbox_id"] is None

    async def test_assignee_dm_skipped_when_notify_assignees_false(self) -> None:
        team_id = uuid.uuid4()
        alice = _recipient(login="alice", external_user_id="111")
        client = FakeTrackerClient(
            issues_by_login={
                "alice": [
                    {
                        "key": "TEST-1",
                        "summary": "task",
                        "deadline": "2026-06-09",
                        "status": {},
                        "assignee": {},
                    }
                ]
            }
        )
        now = datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc)
        session = FakeSession()

        with (
            patch(
                "core.deadline_reminders.get_config",
                return_value=_cfg(notify_assignees=False, notify_lead=False),
            ),
            patch(
                "core.deadline_reminders.load_reminder_recipients",
                AsyncMock(return_value=[alice]),
            ),
        ):
            result = await send_team_deadline_reminders(
                session,
                team_id=team_id,
                now=now,
                client_factory=lambda: client,
            )

        assert result["assignee_outbox_ids"] == []

    async def test_tracker_error_per_member_does_not_abort(self) -> None:
        """If one member's tracker query fails, the rest of the run continues."""
        team_id = uuid.uuid4()
        alice = _recipient(login="alice", external_user_id="111")
        bob = _recipient(login="bob", external_user_id="222")
        client = FakeTrackerClient(
            issues_by_login={
                "bob": [
                    {
                        "key": "TEST-2",
                        "summary": "Bob task",
                        "deadline": "2026-06-09",
                        "status": {},
                        "assignee": {},
                    }
                ]
            }
        )
        now = datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc)
        session = FakeSession()

        original_search = client.search_issues

        async def failing_search(query: str, *, limit: int = 20) -> list[dict]:
            if 'Assignee: "alice"' in query:
                raise RuntimeError("tracker timeout")
            return await original_search(query, limit=limit)

        client.search_issues = failing_search  # type: ignore[method-assign]

        with (
            patch("core.deadline_reminders.get_config", return_value=_cfg(notify_lead=False)),
            patch(
                "core.deadline_reminders.load_reminder_recipients",
                AsyncMock(return_value=[alice, bob]),
            ),
        ):
            result = await send_team_deadline_reminders(
                session,
                team_id=team_id,
                now=now,
                client_factory=lambda: client,
            )

        # alice failed silently; bob enqueued
        assert result["status"] == "enqueued"
        outbox_rows = [o for o in session.added if isinstance(o, TelegramOutbox)]
        bob_dm = next((o for o in outbox_rows if o.target_user_id == "222"), None)
        assert bob_dm is not None


# ===========================================================================
# ensure_deadline_reminder_scheduled_job
# ===========================================================================


class TestEnsureDeadlineReminderScheduledJob:
    async def test_creates_job_when_not_exists(self) -> None:
        team_id = uuid.uuid4()
        session = FakeSession(existing_job=None)

        with (
            patch(
                "core.deadline_reminders.get_config",
                return_value=SimpleNamespace(
                    deadline_reminder=SimpleNamespace(
                        enabled=True, cron_expr="0 * * * *"
                    )
                ),
            ),
            patch(
                "core.seed.ensure_agent_instances",
                AsyncMock(return_value={"pm_agent": SimpleNamespace(id=uuid.uuid4())}),
            ),
        ):
            await ensure_deadline_reminder_scheduled_job(session, team_id)

        job_rows = [o for o in session.added if isinstance(o, ScheduledJob)]
        assert len(job_rows) == 1
        assert job_rows[0].name == DEADLINE_REMINDER_JOB_NAME
        assert job_rows[0].cron_expr == "0 * * * *"
        assert job_rows[0].payload["type"] == DEADLINE_REMINDER_PAYLOAD_TYPE

    async def test_updates_cron_when_changed(self) -> None:
        team_id = uuid.uuid4()
        agent_id = uuid.uuid4()
        existing_job = MagicMock(spec=ScheduledJob)
        existing_job.cron_expr = "0 * * * *"
        existing_job.next_run = None
        existing_job.enabled = True
        session = FakeSession(existing_job=existing_job)

        with (
            patch(
                "core.deadline_reminders.get_config",
                return_value=SimpleNamespace(
                    deadline_reminder=SimpleNamespace(
                        enabled=True, cron_expr="0 13 * * *"
                    )
                ),
            ),
            patch(
                "core.seed.ensure_agent_instances",
                AsyncMock(return_value={"pm_agent": SimpleNamespace(id=agent_id)}),
            ),
        ):
            await ensure_deadline_reminder_scheduled_job(session, team_id)

        # cron_expr updated in-place on the existing object
        assert existing_job.cron_expr == "0 13 * * *"
        assert existing_job.next_run is not None

    async def test_disabled_sets_next_run_none(self) -> None:
        team_id = uuid.uuid4()
        agent_id = uuid.uuid4()
        existing_job = MagicMock(spec=ScheduledJob)
        existing_job.cron_expr = "0 * * * *"
        existing_job.next_run = datetime(2026, 6, 11, 12, tzinfo=timezone.utc)
        existing_job.enabled = True
        session = FakeSession(existing_job=existing_job)

        with (
            patch(
                "core.deadline_reminders.get_config",
                return_value=SimpleNamespace(
                    deadline_reminder=SimpleNamespace(
                        enabled=False, cron_expr="0 * * * *"
                    )
                ),
            ),
            patch(
                "core.seed.ensure_agent_instances",
                AsyncMock(return_value={"pm_agent": SimpleNamespace(id=agent_id)}),
            ),
        ):
            await ensure_deadline_reminder_scheduled_job(session, team_id)

        assert existing_job.next_run is None

    async def test_payload_contains_team_id(self) -> None:
        team_id = uuid.uuid4()
        session = FakeSession(existing_job=None)

        with (
            patch(
                "core.deadline_reminders.get_config",
                return_value=SimpleNamespace(
                    deadline_reminder=SimpleNamespace(
                        enabled=True, cron_expr="0 * * * *"
                    )
                ),
            ),
            patch(
                "core.seed.ensure_agent_instances",
                AsyncMock(return_value={"pm_agent": SimpleNamespace(id=uuid.uuid4())}),
            ),
        ):
            await ensure_deadline_reminder_scheduled_job(session, team_id)

        job_rows = [o for o in session.added if isinstance(o, ScheduledJob)]
        assert job_rows[0].payload["team_id"] == str(team_id)


# ===========================================================================
# Scheduler dispatch
# ===========================================================================


class TestSchedulerDispatch:
    async def test_deadline_reminder_payload_dispatched(self) -> None:
        svc = MagicMock()
        svc._db_enabled = True
        svc.invoke = AsyncMock()
        daemon = SchedulerDaemon(svc)
        team_id = "00000000-0000-0000-0000-000000000001"

        job = MagicMock()
        job.id = uuid.uuid4()
        job.cron_expr = "0 * * * *"
        job.run_count = 0
        job.max_runs = None
        job.enabled = True
        job.payload = {"type": DEADLINE_REMINDER_PAYLOAD_TYPE, "team_id": team_id}
        job.next_run = datetime(2026, 6, 11, tzinfo=timezone.utc)

        session = MagicMock()
        session.flush = AsyncMock()

        with patch(
            "core.deadline_reminders.send_team_deadline_reminders", AsyncMock()
        ) as mock_send:
            await daemon._fire(session, job)

        mock_send.assert_awaited_once_with(session, team_id=team_id)
        svc.invoke.assert_not_awaited()

    async def test_deadline_reminder_missing_team_id_raises(self) -> None:
        svc = MagicMock()
        svc._db_enabled = True
        svc._team_id = None
        svc.invoke = AsyncMock()
        daemon = SchedulerDaemon(svc)

        job = MagicMock()
        job.id = uuid.uuid4()
        job.cron_expr = "0 * * * *"
        job.run_count = 0
        job.max_runs = None
        job.enabled = True
        job.payload = {"type": DEADLINE_REMINDER_PAYLOAD_TYPE}  # no team_id
        job.next_run = datetime(2026, 6, 11, tzinfo=timezone.utc)

        session = MagicMock()
        session.flush = AsyncMock()

        # Error is caught per-job — run_count still updated, no re-raise
        with patch("core.deadline_reminders.send_team_deadline_reminders", AsyncMock()):
            await daemon._fire(session, job)

        # run_count incremented even on error
        assert job.run_count == 1
