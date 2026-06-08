from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.assignee_resolver import TrackerUser
from core.daily_digest import (
    DAILY_DIGEST_JOB_NAME,
    DigestIssue,
    DigestMember,
    DigestReport,
    build_daily_digest_report,
    day_window_utc,
    ensure_daily_digest_scheduled_job,
    format_daily_digest,
    split_telegram_text,
)
from core.models import ScheduledJob, TelegramOutbox


def _cfg(
    *,
    statuses: str = "In Progress,\u0412 \u0440\u0430\u0431\u043e\u0442\u0435",
    max_issues: int = 10,
    enabled: bool = True,
    cron: str = "0 * * * *",
) -> SimpleNamespace:
    digest = SimpleNamespace(
        enabled=enabled,
        cron_expr=cron,
        timezone="Europe/Moscow",
        telegram_chat_id="",
        in_progress_statuses=statuses,
        max_issues_per_section=max_issues,
        in_progress_status_list=lambda: [
            part.strip() for part in statuses.split(",") if part.strip()
        ],
    )
    return SimpleNamespace(
        daily_digest=digest,
        tracker=SimpleNamespace(tracker_queue="TEST"),
    )


class FakeSession:
    def __init__(self, *, team_queue: str = "TEST", existing_job=None) -> None:
        self.team_queue = team_queue
        self.existing_job = existing_job
        self.legacy_jobs: list[object] = []
        self.added: list[object] = []
        self.flushed = False

    async def get(self, model, key):
        del model, key
        return SimpleNamespace(tracker_queue=self.team_queue)

    async def execute(self, stmt):
        del stmt

        class Result:
            def __init__(self, job, legacy_jobs):
                self.job = job
                self.legacy_jobs = legacy_jobs

            def scalar_one_or_none(self):
                return self.job

            def scalars(self):
                return self

            def all(self):
                return self.legacy_jobs

        return Result(self.existing_job, self.legacy_jobs)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True


class FakeTrackerClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def search_issues(self, query: str, *, limit: int = 20):
        del limit
        self.queries.append(query)
        if "Resolution: !empty()" in query:
            return [
                {
                    "key": "TEST-2",
                    "summary": "Ship report",
                    "status": {"display": "Closed"},
                    "assignee": {"login": "alice", "display": "Alice"},
                }
            ]
        return [
            {
                "key": "TEST-1",
                "summary": "Build bot",
                "status": {"display": "In Progress"},
                "assignee": {"login": "alice", "display": "Alice"},
            }
        ]


def test_day_window_uses_moscow_date() -> None:
    local_date, start_utc, end_utc = day_window_utc(
        datetime(2026, 6, 8, 20, 30, tzinfo=timezone.utc),
        timezone_name="Europe/Moscow",
    )

    assert local_date == "2026-06-08"
    assert start_utc == datetime(2026, 6, 7, 21, 0, tzinfo=timezone.utc)
    assert end_utc == datetime(2026, 6, 8, 21, 0, tzinfo=timezone.utc)


async def test_build_report_groups_by_tracker_team_members() -> None:
    client = FakeTrackerClient()

    with (
        patch("core.daily_digest.get_config", return_value=_cfg()),
        patch(
            "core.daily_digest.load_team_users",
            AsyncMock(
                return_value=[
                    TrackerUser(login="alice", display="Alice"),
                    TrackerUser(login="bob", display="Bob"),
                ]
            ),
        ),
    ):
        report = await build_daily_digest_report(
            FakeSession(team_queue="DARKHORSE"),
            team_id=uuid.uuid4(),
            now=datetime(2026, 6, 8, 15, 0, tzinfo=timezone.utc),
            client_factory=lambda: client,
        )

    assert report.queue == "DARKHORSE"
    alice = report.members[0]
    bob = report.members[1]
    assert [issue.key for issue in alice.in_progress] == ["TEST-1"]
    assert [issue.key for issue in alice.done_today] == ["TEST-2"]
    assert bob.in_progress == []
    assert bob.done_today == []
    assert any("Updated: >=" in query for query in client.queries)


def test_format_report_includes_empty_member_sections() -> None:
    report = DigestReport(
        team_id=uuid.uuid4(),
        queue="TEST",
        local_date="2026-06-08",
        local_hour="2026-06-08T18",
        timezone="Europe/Moscow",
        members=[
            DigestMember(
                login="alice",
                display="Alice",
                in_progress=[
                    DigestIssue(
                        key="TEST-1",
                        summary="Build bot",
                        status="In Progress",
                        assignee_login="alice",
                        assignee_display="Alice",
                        url="https://tracker.yandex.ru/TEST-1",
                    )
                ],
                done_today=[],
            )
        ],
    )

    with patch("core.daily_digest.get_config", return_value=_cfg()):
        text = format_daily_digest(report)

    assert "TEST-1" in text
    assert "Сделано сегодня:" in text
    assert "- нет задач" in text


def test_split_telegram_text_preserves_limit() -> None:
    parts = split_telegram_text("a\n" + ("b" * 20) + "\nc", limit=10)

    assert all(len(part) <= 10 for part in parts)
    assert "".join(parts).replace("\n", "") == ("a" + ("b" * 20) + "c")


async def test_enqueue_digest_messages_creates_stable_dedupe_keys() -> None:
    from core.daily_digest import _enqueue_digest_messages

    team_id = uuid.uuid4()
    chat = SimpleNamespace(
        id=uuid.uuid4(),
        installation_id=uuid.uuid4(),
        external_chat_id="-1001",
    )

    class Session(FakeSession):
        async def execute(self, stmt):
            del stmt

            class Result:
                def scalar_one_or_none(self):
                    return None

            return Result()

    session = Session()
    ids = await _enqueue_digest_messages(
        session,
        team_id=team_id,
        chat=chat,
        dedupe_slot="2026-06-08T18",
        local_date="2026-06-08",
        text="hello",
    )

    assert len(ids) == 1
    assert isinstance(session.added[0], TelegramOutbox)
    assert session.added[0].dedupe_key == f"daily-digest:{team_id}:2026-06-08T18:-1001:part:1"


async def test_ensure_daily_digest_job_is_idempotent() -> None:
    team_id = uuid.uuid4()
    agent_instance = SimpleNamespace(id=uuid.uuid4())
    session = FakeSession()
    first_run = datetime(2026, 6, 8, 15, 0, tzinfo=timezone.utc)

    with (
        patch("core.daily_digest.get_config", return_value=_cfg()),
        patch(
            "core.seed.ensure_agent_instances",
            AsyncMock(return_value={"pm_agent": agent_instance}),
        ),
        patch("core.scheduler.compute_next_run", return_value=first_run),
    ):
        await ensure_daily_digest_scheduled_job(session, team_id)
        session.existing_job = session.added[0]
        await ensure_daily_digest_scheduled_job(session, team_id)

    assert len(session.added) == 1
    job = session.added[0]
    assert isinstance(job, ScheduledJob)
    assert job.name == DAILY_DIGEST_JOB_NAME
    assert job.payload["type"] == "team_daily_digest"
    assert job.enabled is True


async def test_ensure_daily_digest_job_disables_legacy_job() -> None:
    team_id = uuid.uuid4()
    agent_instance = SimpleNamespace(id=uuid.uuid4())
    legacy = SimpleNamespace(
        id=uuid.uuid4(),
        name="team_daily_digest_msk_1800",
        enabled=True,
    )
    session = FakeSession()
    session.legacy_jobs = [legacy]

    with (
        patch("core.daily_digest.get_config", return_value=_cfg()),
        patch(
            "core.seed.ensure_agent_instances",
            AsyncMock(return_value={"pm_agent": agent_instance}),
        ),
        patch("core.scheduler.compute_next_run", return_value=datetime(2026, 6, 8, 15, 0)),
    ):
        await ensure_daily_digest_scheduled_job(session, team_id)

    assert legacy.enabled is False
