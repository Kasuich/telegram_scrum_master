from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from core.models import (
    Team,
    TeamMembership,
    TelegramInstallation,
    TelegramStandupPoll,
    TelegramUser,
)
from core.standup_poll import (
    PollIssue,
    RegisteredParticipant,
    build_member_issues_yql,
    format_standup_poll_message,
    handle_standup_response,
    load_registered_participants,
    parse_standup_response,
    poll_digest_hour_key,
)


def test_poll_digest_hour_key_uses_next_digest_slot() -> None:
    key = poll_digest_hour_key(
        datetime(2026, 6, 8, 7, 50, tzinfo=timezone.utc),
        timezone_name="Europe/Moscow",
        lead_minutes=10,
    )

    assert key == "2026-06-08T11"


def test_build_member_issues_yql_uses_board_query() -> None:
    yql = build_member_issues_yql(
        queue="TEST",
        tracker_login="alice",
        board={"query": 'Queue: "DARKHORSE" AND Status: !"Closed"'},
    )

    assert 'Queue: "DARKHORSE"' in yql
    assert 'Assignee: "alice"' in yql
    assert "Resolution: empty()" in yql


def test_build_member_issues_yql_translates_simple_filter() -> None:
    yql = build_member_issues_yql(
        queue="TEST",
        tracker_login="alice",
        board={"filter": {"queue": "DARKHORSE", "status": ["Open", "In Progress"]}},
    )

    assert 'Queue: "DARKHORSE"' in yql
    assert 'Status: "Open"' in yql
    assert 'Status: "In Progress"' in yql


def test_build_member_issues_yql_falls_back_to_queue() -> None:
    yql = build_member_issues_yql(
        queue="TEST",
        tracker_login="alice",
        board={"filter": {"nested": {"unsupported": True}}},
    )

    assert 'Queue: "TEST"' in yql
    assert 'Assignee: "alice"' in yql


def test_format_standup_poll_message_numbers_tasks() -> None:
    participant = RegisteredParticipant(
        team_id=uuid.uuid4(),
        installation_id=uuid.uuid4(),
        telegram_user_id=uuid.uuid4(),
        external_user_id="991",
        user_id=uuid.uuid4(),
        tracker_login="alice",
        display="Alice",
        board_id="3",
        board_name="Product",
    )
    text = format_standup_poll_message(
        participant=participant,
        local_hour="2026-06-08T11",
        issues=[
            PollIssue(
                number=1,
                key="TEST-1",
                summary="Build bot",
                status="In Progress",
                url="https://tracker.yandex.ru/TEST-1",
            )
        ],
    )

    assert "1. TEST-1" in text
    assert "задача 1 закрыта" in text


def test_parse_standup_response() -> None:
    text = (
        "задача 1 закрыта\n"
        "задача 2 задерживается: жду доступ\n"
        "новая задача: подготовить демо"
    )
    actions = parse_standup_response(text)

    assert [action.kind for action in actions] == ["close", "blocked", "create"]
    assert actions[0].issue_number == 1
    assert actions[2].text == "подготовить демо"


def test_parse_multiple_task_markers_in_one_line() -> None:
    actions = parse_standup_response(
        "задача 5 закрыта задача 11 нужно больше информации"
    )

    assert [(action.kind, action.issue_number) for action in actions] == [
        ("close", 5),
        ("comment", 11),
    ]
    assert actions[1].text == "задача 11 нужно больше информации"


def test_parse_numbered_list_cancel_and_dash_new_task() -> None:
    text = (
        "1) Транскрибация тестируется, нужен 1 день. "
        "4) Телеграм проверен, можно закрывать "
        "5) Отмени задачу "
        "6) Отмени задачу "
        "Новая задача - поспать"
    )

    actions = parse_standup_response(text)

    assert [(action.kind, action.issue_number) for action in actions] == [
        ("comment", 1),
        ("close", 4),
        ("cancel", 5),
        ("cancel", 6),
        ("create", None),
    ]
    assert actions[-1].text == "поспать"


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _ParticipantSession:
    def __init__(self, rows) -> None:
        self.rows = rows

    async def execute(self, stmt):
        del stmt
        return _Result(self.rows)


async def test_load_registered_participants_returns_confirmed_links() -> None:
    team_id = uuid.uuid4()
    installation = TelegramInstallation(
        id=uuid.uuid4(),
        team_id=team_id,
        alias="bot",
        mode="workspace_bot",
        status="active",
        settings={},
    )
    user = TelegramUser(
        id=uuid.uuid4(),
        external_user_id="991",
        first_name="Alice",
        is_bot=False,
        is_blocked=False,
    )
    link = SimpleNamespace(
        team_id=team_id,
        telegram_user_id=user.id,
        installation_id=installation.id,
        status="active",
    )
    membership = TeamMembership(
        team_id=team_id,
        user_id=uuid.uuid4(),
        tracker_login="alice",
        tracker_display_name="Alice Tracker",
        tracker_match_status="confirmed",
        default_board_id="3",
        settings_json={"default_board_name": "Product"},
    )

    participants = await load_registered_participants(
        _ParticipantSession([(link, membership, user, installation)]),
        team_id=team_id,
    )

    assert len(participants) == 1
    assert participants[0].tracker_login == "alice"
    assert participants[0].board_id == "3"
    assert participants[0].board_name == "Product"


class _ResponseSession:
    def __init__(self, poll: TelegramStandupPoll) -> None:
        self.poll = poll
        self.flushed = False

    async def execute(self, stmt):
        del stmt
        return _Result(self.poll)

    async def get(self, model, key):
        del key
        if model is Team:
            return SimpleNamespace(tracker_queue="TEST")
        return None

    async def flush(self):
        self.flushed = True


class _FakeTracker:
    def __init__(self) -> None:
        self.transitions: list[tuple[str, str, str | None]] = []
        self.comments: list[tuple[str, str]] = []
        self.created: list[tuple[str, str, str | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def transition_issue(self, issue_key, transition_id, *, comment=None, resolution=None):
        del resolution
        self.transitions.append((issue_key, transition_id, comment))
        return {"id": transition_id}

    async def comment_issue(self, issue_key, text):
        self.comments.append((issue_key, text))
        return {"id": "c1"}

    async def create_issue(self, queue, summary, *, assignee=None, **kwargs):
        del kwargs
        self.created.append((queue, summary, assignee))
        return {"key": "TEST-3", "summary": summary}


async def test_handle_standup_response_applies_changes() -> None:
    team_id = uuid.uuid4()
    telegram_user_id = uuid.uuid4()
    poll = TelegramStandupPoll(
        id=uuid.uuid4(),
        team_id=team_id,
        installation_id=uuid.uuid4(),
        telegram_user_id=telegram_user_id,
        user_id=uuid.uuid4(),
        tracker_login="alice",
        local_hour="2026-06-08T11",
        issues_json=[
            {"number": 1, "key": "TEST-1", "summary": "Build bot"},
            {"number": 2, "key": "TEST-2", "summary": "Deploy bot"},
        ],
        applied_json={},
        status="pending",
    )
    tracker = _FakeTracker()

    reply = await handle_standup_response(
        _ResponseSession(poll),
        team_id=team_id,
        telegram_user_id=telegram_user_id,
        text=(
            "задача 1 закрыта\n"
            "задача 2 задерживается: жду доступ\n"
            "новая задача: подготовить демо"
        ),
        client_factory=lambda: tracker,
    )

    assert reply is not None
    assert poll.status == "answered"
    assert tracker.transitions[0][0] == "TEST-1"
    assert tracker.comments == [
        ("TEST-2", "задача 2 задерживается: жду доступ")
    ]
    assert tracker.created == [("TEST", "подготовить демо", "alice")]
    assert "TEST-3" in reply
