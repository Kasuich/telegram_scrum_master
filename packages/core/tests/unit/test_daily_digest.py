from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.daily_digest import (
    DAILY_DIGEST_JOB_NAME,
    DigestChatUpdate,
    DigestIssue,
    DigestMember,
    DigestReport,
    DigestSprint,
    _resolve_digest_chat,
    build_daily_digest_report,
    build_done_today_yql,
    completed_hour_window_utc,
    day_window_utc,
    ensure_daily_digest_scheduled_job,
    format_daily_digest,
    split_telegram_text,
)
from core.models import ScheduledJob, TelegramOutbox
from sqlalchemy.dialects import postgresql


class _StmtCapture:
    """Async session stub that records the executed statement and returns no rows."""

    def __init__(self) -> None:
        self.stmt = None

    async def execute(self, stmt):
        self.stmt = stmt
        return _EmptyResult()


class _EmptyResult:
    def scalars(self):
        return self

    def all(self):
        return []


def _compiled_sql(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


async def test_resolve_digest_chat_does_not_filter_on_installation_mode() -> None:
    # Regression: prod installation.mode was "webhook" (transport bookkeeping),
    # which the old query required to equal "workspace_bot" — silently yielding
    # zero digest chats. The authoritative signal is chat.access_mode.
    session = _StmtCapture()
    await _resolve_digest_chat(session, team_id=uuid.uuid4())
    sql = _compiled_sql(session.stmt)
    assert "telegram_installations.mode = 'workspace_bot'" not in sql
    assert "telegram_chats.access_mode = 'workspace_bot'" in sql


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
        max_sprint_issues=30,
        max_chat_messages=20,
        in_progress_status_list=lambda: [
            part.strip() for part in statuses.split(",") if part.strip()
        ],
    )
    return SimpleNamespace(
        daily_digest=digest,
        standup_poll=SimpleNamespace(enabled=True, cron_expr="50 * * * *"),
        tracker=SimpleNamespace(tracker_queue="TEST"),
    )


class FakeSession:
    def __init__(self, *, team_queue: str = "TEST", existing_job=None) -> None:
        self.team_queue = team_queue
        self.existing_job = existing_job
        self.execute_results: list[object] = []
        self.legacy_jobs: list[object] = []
        self.added: list[object] = []
        self.flushed = False

    async def get(self, model, key):
        del model, key
        return SimpleNamespace(tracker_queue=self.team_queue)

    async def execute(self, stmt):
        del stmt

        class Result:
            def __init__(self, value, legacy_jobs):
                self.value = value
                self.legacy_jobs = legacy_jobs

            def scalar_one_or_none(self):
                return self.value

            def scalars(self):
                return self

            def all(self):
                if isinstance(self.value, list):
                    return self.value
                return self.legacy_jobs

        if self.execute_results:
            return Result(self.execute_results.pop(0), self.legacy_jobs)
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


class EmptyTrackerClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def search_issues(self, query: str, *, limit: int = 20):
        del limit
        self.queries.append(query)
        return []


def test_day_window_uses_moscow_date() -> None:
    local_date, start_utc, end_utc = day_window_utc(
        datetime(2026, 6, 8, 20, 30, tzinfo=timezone.utc),
        timezone_name="Europe/Moscow",
    )

    assert local_date == "2026-06-08"
    assert start_utc == datetime(2026, 6, 7, 21, 0, tzinfo=timezone.utc)
    assert end_utc == datetime(2026, 6, 8, 21, 0, tzinfo=timezone.utc)


def test_completed_hour_window_uses_previous_clock_hour() -> None:
    start, end = completed_hour_window_utc(
        datetime(2026, 6, 8, 8, 0, 12, tzinfo=timezone.utc)
    )

    assert start == datetime(2026, 6, 8, 7, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 6, 8, 8, 0, tzinfo=timezone.utc)


async def test_load_current_sprints_uses_registered_board_and_sprint_tasks() -> None:
    from core.daily_digest import _load_current_sprints

    class Client:
        def __init__(self) -> None:
            self.query = ""

        async def list_sprints(self, board_id):
            assert board_id == "3"
            return [
                {
                    "id": 44,
                    "name": "Sprint 9",
                    "archived": True,
                    "startDate": "2026-05-25",
                    "endDate": "2026-06-07",
                },
                {
                    "id": 45,
                    "name": "Sprint 10",
                    "archived": False,
                    "status": "inProgress",
                    "startDate": "2026-06-08",
                    "endDate": "2026-06-21",
                },
            ]

        async def get_board(self, board_id):
            assert board_id == "3"
            return {"query": 'Queue: "PRODUCT"'}

        async def search_all_issues(self, query, *, queue, page_size):
            self.query = query
            assert queue is None
            assert page_size == 30
            return [
                {
                    "key": "TEST-7",
                    "summary": "Prepare demo",
                    "status": {"display": "In Progress"},
                    "assignee": {"login": "alice", "display": "Alice"},
                    "sprint": [{"id": "45"}],
                }
            ]

    client = Client()
    sprints = await _load_current_sprints(
        client,
        queue="TEST",
        members=[SimpleNamespace(board_id="3", board_name="Product")],
        local_date=datetime(2026, 6, 8).date(),
        max_issues=30,
    )

    assert client.query == '(Queue: "PRODUCT") AND (Sprint: "Sprint 10")'
    assert len(sprints) == 1
    assert sprints[0].sprint_name == "Sprint 10"
    assert sprints[0].issues[0].key == "TEST-7"


async def test_load_chat_updates_returns_messages_for_completed_hour() -> None:
    from core.daily_digest import _load_chat_updates

    message = SimpleNamespace(
        id=uuid.uuid4(),
        text="Обновили макеты и договорились о демо",
        caption=None,
        sent_at=datetime(2026, 6, 8, 7, 25, tzinfo=timezone.utc),
    )
    user = SimpleNamespace(first_name="Alice", last_name=None, username="alice")

    class Session:
        async def execute(self, stmt):
            del stmt

            class Result:
                def all(self):
                    return [(message, user)]

            return Result()

    updates = await _load_chat_updates(
        Session(),
        team_id=uuid.uuid4(),
        chat_id=uuid.uuid4(),
        start_utc=datetime(2026, 6, 8, 7, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 6, 8, 8, 0, tzinfo=timezone.utc),
        timezone_name="Europe/Moscow",
        limit=20,
    )

    assert updates == [
        DigestChatUpdate(
            author="Alice",
            text="Обновили макеты и договорились о демо",
            local_time="10:25",
        )
    ]


def test_format_hourly_digest_includes_sprint_responses_and_chat() -> None:
    issue = DigestIssue(
        key="TEST-7",
        summary="Prepare demo",
        status="In Progress",
        assignee_login="alice",
        assignee_display="Alice",
        url="https://tracker.yandex.ru/TEST-7",
    )
    report = DigestReport(
        team_id=uuid.uuid4(),
        queue="TEST",
        local_date="2026-06-08",
        local_hour="2026-06-08T11",
        timezone="Europe/Moscow",
        members=[
            DigestMember(
                login="alice",
                display="Alice",
                in_progress=[],
                done_today=[],
                standup_response="Сделала ревью и подготовила демо",
                board_name="Product",
                responded=True,
            ),
            DigestMember(
                login="bob",
                display="Bob",
                in_progress=[],
                done_today=[],
                board_name="Product",
                responded=False,
            ),
        ],
        sprints=[
            DigestSprint(
                board_id="3",
                board_name="Product",
                sprint_id="45",
                sprint_name="Sprint 10",
                status="inProgress",
                start_date="2026-06-08",
                end_date="2026-06-21",
                issues=[issue],
            )
        ],
        chat_updates=[
            DigestChatUpdate(
                author="Alice",
                text="Демо перенесли на 15:00",
                local_time="10:25",
            )
        ],
    )

    with patch("core.daily_digest.get_config", return_value=_cfg()):
        text = format_daily_digest(report)

    assert "Ежечасный отчёт · 11:00" in text
    assert "Product · Sprint 10" in text
    assert "TEST-7" in text
    assert "Сделала ревью и подготовила демо" in text
    assert "ответа на опрос нет" in text
    assert "Демо перенесли на 15:00" in text


def test_done_today_yql_uses_tracker_date_literals() -> None:
    yql = build_done_today_yql("DARKHORSE", "2026-06-08")

    assert 'Updated: >= "2026-06-08"' in yql
    assert 'Updated: < "2026-06-09"' in yql
    assert "T21:00:00Z" not in yql


async def test_build_report_groups_by_registered_telegram_members() -> None:
    client = FakeTrackerClient()

    with (
        patch("core.daily_digest.get_config", return_value=_cfg()),
        patch(
            "core.daily_digest.load_registered_participants",
            AsyncMock(
                return_value=[
                    SimpleNamespace(tracker_login="alice", display="Alice"),
                    SimpleNamespace(tracker_login="bob", display="Bob"),
                ]
            ),
        ),
        patch("core.daily_digest._load_standup_polls_by_login", AsyncMock(return_value={})),
    ):
        report = await build_daily_digest_report(
            FakeSession(team_queue="DARKHORSE"),
            team_id=uuid.uuid4(),
            now=datetime(2026, 6, 8, 15, 0, tzinfo=timezone.utc),
            client_factory=lambda: client,
        )

    assert report.queue == "DARKHORSE"
    assert [member.login for member in report.members] == ["alice", "bob"]
    assert all(not member.responded for member in report.members)
    assert client.queries == []


async def test_build_report_uses_poll_tasks_when_tracker_sections_are_empty() -> None:
    client = EmptyTrackerClient()
    team_id = uuid.uuid4()
    poll = SimpleNamespace(
        tracker_login="alice",
        response_text=("задача 2 закрыта задача 1 нужно больше информации"),
        issues_json=[
            {
                "number": 1,
                "key": "TEST-1",
                "summary": "Need info",
                "status": "Open",
                "url": "https://tracker.yandex.ru/TEST-1",
            },
            {
                "number": 2,
                "key": "TEST-2",
                "summary": "Done task",
                "status": "Open",
                "url": "https://tracker.yandex.ru/TEST-2",
            },
        ],
        applied_json={
            "results": [
                {
                    "kind": "comment",
                    "issue_key": "TEST-1",
                    "issue_number": 1,
                    "ok": True,
                },
                {
                    "kind": "close",
                    "issue_key": "TEST-2",
                    "issue_number": 2,
                    "ok": True,
                },
            ]
        },
    )

    with (
        patch("core.daily_digest.get_config", return_value=_cfg()),
        patch(
            "core.daily_digest.load_registered_participants",
            AsyncMock(return_value=[SimpleNamespace(tracker_login="alice", display="Alice")]),
        ),
        patch(
            "core.daily_digest._load_standup_polls_by_login",
            AsyncMock(return_value={"alice": poll}),
        ),
    ):
        report = await build_daily_digest_report(
            FakeSession(team_queue="DARKHORSE"),
            team_id=team_id,
            now=datetime(2026, 6, 8, 15, 0, tzinfo=timezone.utc),
            client_factory=lambda: client,
        )

    alice = report.members[0]
    assert alice.in_progress == []
    assert alice.done_today == []
    assert "Комментарии" in alice.sections
    assert "Статусы" in alice.sections
    assert any("TEST-1" in line for line in alice.sections["Комментарии"])
    assert any("TEST-2" in line for line in alice.sections["Статусы"])


async def test_build_report_uses_all_poll_history_actions_only() -> None:
    client = EmptyTrackerClient()
    team_id = uuid.uuid4()
    poll = SimpleNamespace(
        tracker_login="alice",
        response_text="old fallback",
        issues_json=[
            {
                "number": 1,
                "key": "TEST-1",
                "summary": "Needs data",
                "status": "Open",
                "url": "https://tracker.yandex.ru/TEST-1",
            },
            {
                "number": 2,
                "key": "TEST-2",
                "summary": "Deploy bot",
                "status": "Open",
                "url": "https://tracker.yandex.ru/TEST-2",
            },
            {
                "number": 3,
                "key": "TEST-3",
                "summary": "Untouched task",
                "status": "Open",
                "url": "https://tracker.yandex.ru/TEST-3",
            },
        ],
        applied_json={
            "responses": [
                {
                    "text": "task 1 need more info",
                    "results": [
                        {
                            "kind": "comment",
                            "issue_key": "TEST-1",
                            "issue_number": 1,
                            "ok": True,
                        }
                    ],
                },
                {
                    "text": "task 2 done new task: write release notes",
                    "results": [
                        {
                            "kind": "close",
                            "issue_key": "TEST-2",
                            "issue_number": 2,
                            "ok": True,
                        },
                        {
                            "kind": "create",
                            "issue_key": "TEST-4",
                            "summary": "write release notes",
                            "ok": True,
                        },
                        {
                            "kind": "comment",
                            "issue_number": 99,
                            "ok": False,
                            "error": "unknown_issue_number",
                        },
                    ],
                },
            ],
            "results": [
                {
                    "kind": "comment",
                    "issue_key": "TEST-1",
                    "issue_number": 1,
                    "ok": True,
                },
                {
                    "kind": "close",
                    "issue_key": "TEST-2",
                    "issue_number": 2,
                    "ok": True,
                },
                {
                    "kind": "create",
                    "issue_key": "TEST-4",
                    "summary": "write release notes",
                    "ok": True,
                },
                {
                    "kind": "comment",
                    "issue_number": 99,
                    "ok": False,
                    "error": "unknown_issue_number",
                },
            ],
        },
    )

    with (
        patch("core.daily_digest.get_config", return_value=_cfg()),
        patch(
            "core.daily_digest.load_registered_participants",
            AsyncMock(return_value=[SimpleNamespace(tracker_login="alice", display="Alice")]),
        ),
        patch(
            "core.daily_digest._load_standup_polls_by_login",
            AsyncMock(return_value={"alice": poll}),
        ),
    ):
        report = await build_daily_digest_report(
            FakeSession(team_queue="DARKHORSE"),
            team_id=team_id,
            now=datetime(2026, 6, 8, 15, 0, tzinfo=timezone.utc),
            client_factory=lambda: client,
        )

    alice = report.members[0]
    assert alice.in_progress == []
    assert alice.done_today == []
    assert set(alice.sections) == {"Комментарии", "Статусы", "Создано", "Не применено"}
    assert any("TEST-1" in line for line in alice.sections["Комментарии"])
    assert any("TEST-2" in line for line in alice.sections["Статусы"])
    assert any("TEST-4" in line for line in alice.sections["Создано"])
    assert alice.standup_response == (
        "task 1 need more info\n\ntask 2 done new task: write release notes"
    )
    assert len(alice.applied_items) == 4
    assert any("99" in item for item in alice.applied_items)


async def test_build_report_ignores_reported_polls() -> None:
    client = EmptyTrackerClient()
    team_id = uuid.uuid4()

    with (
        patch("core.daily_digest.get_config", return_value=_cfg()),
        patch(
            "core.daily_digest.load_registered_participants",
            AsyncMock(return_value=[SimpleNamespace(tracker_login="alice", display="Alice")]),
        ),
        patch("core.daily_digest._load_standup_polls_by_login", AsyncMock(return_value={})),
    ):
        report = await build_daily_digest_report(
            FakeSession(team_queue="DARKHORSE"),
            team_id=team_id,
            now=datetime(2026, 6, 8, 15, 0, tzinfo=timezone.utc),
            client_factory=lambda: client,
        )

    assert len(report.members) == 1
    assert report.members[0].responded is False


def test_format_report_omits_empty_sections() -> None:
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
                in_progress=[],
                done_today=[],
                sections={"Статусы": ["- TEST-1: закрыто. Текст: task 1 done"]},
            )
        ],
    )

    with patch("core.daily_digest.get_config", return_value=_cfg()):
        text = format_daily_digest(report)

    assert "TEST-1" in text
    assert "Статусы:" in text
    assert "В работе:" not in text
    assert "Сделано сегодня:" not in text
    assert "нет задач" not in text


def test_format_report_shows_single_empty_period_line() -> None:
    report = DigestReport(
        team_id=uuid.uuid4(),
        queue="TEST",
        local_date="2026-06-08",
        local_hour="2026-06-08T18",
        timezone="Europe/Moscow",
        members=[],
    )

    with patch("core.daily_digest.get_config", return_value=_cfg()):
        text = format_daily_digest(report)

    assert "зарегистрированные участники не найдены" in text


async def test_mark_standup_polls_reported_closes_period() -> None:
    from core.daily_digest import _mark_standup_polls_reported

    poll = SimpleNamespace(status="answered")

    class Session(FakeSession):
        async def execute(self, stmt):
            del stmt

            class Result:
                def scalars(self):
                    return self

                def all(self):
                    return [poll]

            return Result()

    await _mark_standup_polls_reported(
        Session(),
        team_id=uuid.uuid4(),
        local_hour="2026-06-08T18",
    )

    assert poll.status == "reported"


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
        session.execute_results = [None, None, []]
        await ensure_daily_digest_scheduled_job(session, team_id)
        session.execute_results = [session.added[0], session.added[1], []]
        await ensure_daily_digest_scheduled_job(session, team_id)

    assert len(session.added) == 2
    digest_job, poll_job = session.added
    assert isinstance(digest_job, ScheduledJob)
    assert digest_job.name == DAILY_DIGEST_JOB_NAME
    assert digest_job.payload["type"] == "team_daily_digest"
    assert digest_job.enabled is True
    assert poll_job.name == "team_hourly_standup_poll"
    assert poll_job.payload["type"] == "team_standup_poll"
    assert poll_job.enabled is True


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
        session.execute_results = [None, None, [legacy]]
        await ensure_daily_digest_scheduled_job(session, team_id)

    assert legacy.enabled is False
