from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from core.assignee_resolver import TrackerUser
from core.models import TelegramInstallation, TelegramOnboardingSession, TelegramUser
from platform_api.telegram_auth import (
    _enqueue_message,
    _find_board,
    _find_tracker_user,
    _is_no,
    _is_yes,
    _suggest_tracker_user,
    start_onboarding,
)


def test_find_tracker_user_accepts_exact_login_email_or_display() -> None:
    users = [
        TrackerUser(
            login="ivan.petrov",
            display="Ivan Petrov",
            email="ivan.petrov@yandex.ru",
        ),
        TrackerUser(login="petr.ivanov", display="Petr Ivanov"),
    ]

    assert _find_tracker_user("@IVAN.PETROV", users) == users[0]
    assert _find_tracker_user("ivan.petrov@yandex.ru", users) == users[0]
    assert _find_tracker_user("  Ivan   Petrov ", users) == users[0]
    assert _find_tracker_user("Ivan", users) is None


def test_suggest_tracker_user_by_similarity_requires_confirmation_later() -> None:
    users = [
        TrackerUser(login="shinkarenkorom", display="Roman Shinkarenko"),
        TrackerUser(login="nukolaus", display="Nikolai Alexandrov"),
    ]

    assert _suggest_tracker_user("Roman Shinkarenko", users) == users[0]
    assert _suggest_tracker_user("Roman Shinkarenko typo", users) == users[0]


def test_confirmation_answers() -> None:
    assert _is_yes("Да")
    assert _is_yes("это я")
    assert _is_no("Нет")
    assert not _is_yes("возможно")


def test_private_authorization_message_targets_telegram_user() -> None:
    session = MagicMock()
    installation = TelegramInstallation(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        alias="pm_bot",
        mode="workspace_bot",
        status="active",
        settings={},
    )

    outbox = _enqueue_message(
        session,
        installation=installation,
        target_chat_id="991",
        text="Authorize",
        category="authorization",
        dedupe_key="auth:1",
    )

    assert outbox.target_chat_id == "991"
    assert outbox.target_user_id == "991"
    session.add.assert_called_once_with(outbox)


def test_authorization_message_supports_inline_buttons() -> None:
    session = MagicMock()
    installation = TelegramInstallation(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        alias="pm_bot",
        mode="workspace_bot",
        status="active",
        settings={},
    )
    reply_markup = {
        "inline_keyboard": [[{"text": "Да, это я", "callback_data": "token"}]]
    }

    outbox = _enqueue_message(
        session,
        installation=installation,
        target_chat_id="991",
        text="Это вы?",
        category="authorization",
        dedupe_key="auth:buttons",
        reply_markup=reply_markup,
    )

    assert outbox.payload["reply_markup"] == reply_markup


def test_find_board_accepts_exact_name_or_id() -> None:
    boards = [{"id": 3, "name": "Product Development"}]

    assert _find_board("3", boards) == boards[0]
    assert _find_board("product development", boards) == boards[0]
    assert _find_board("Product", boards) is None


async def test_group_onboarding_explicitly_requires_authorization(monkeypatch) -> None:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    installation = TelegramInstallation(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        alias="pm_bot",
        mode="workspace_bot",
        status="active",
        settings={"bot_username": "pm_bot"},
    )
    telegram_user = TelegramUser(
        id=uuid.uuid4(),
        external_user_id="991",
        is_bot=False,
        is_blocked=False,
    )
    onboarding = TelegramOnboardingSession(
        id=uuid.uuid4(),
        team_id=installation.team_id,
        installation_id=installation.id,
        telegram_user_id=telegram_user.id,
        status="pending",
        step_key="tracker_login",
        answers_json={},
        attempts=0,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    monkeypatch.setattr(
        "platform_api.telegram_auth._pending_session",
        AsyncMock(return_value=onboarding),
    )

    await start_onboarding(
        session,
        installation=installation,
        telegram_user=telegram_user,
        source_chat_id="-100123",
        source_message_id="42",
        source_is_private=False,
    )

    group_message = next(
        call.args[0]
        for call in session.add.call_args_list
        if getattr(call.args[0], "target_chat_id", None) == "-100123"
    )
    assert "нужно пройти авторизацию" in group_message.payload["text"]
    assert group_message.payload["reply_to_message_id"] == "42"
