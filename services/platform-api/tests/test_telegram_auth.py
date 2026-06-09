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
    start_onboarding,
)


def test_find_tracker_user_requires_exact_login() -> None:
    users = [
        TrackerUser(login="ivan.petrov", display="Ivan Petrov"),
        TrackerUser(login="petr.ivanov", display="Petr Ivanov"),
    ]

    assert _find_tracker_user("@IVAN.PETROV", users) == users[0]
    assert _find_tracker_user("Ivan Petrov", users) is None


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
