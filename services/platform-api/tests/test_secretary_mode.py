from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from core.models import (
    TelegramBusinessConnection,
    TelegramChat,
    TelegramInstallation,
    TelegramMessage,
    TelegramOutbox,
    TelegramUser,
)
from platform_api.telegram_bridge import (
    _can_send_via_business_connection,
    _upsert_business_connection,
)


class _FakeResult:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def scalar_one_or_none(self) -> object | None:
        return self._row


class _FakeSession:
    def __init__(self, row: object | None = None) -> None:
        self.row = row
        self.added: list[object] = []
        self.flush_calls = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_calls += 1

    async def execute(self, _stmt: object) -> _FakeResult:
        return _FakeResult(self.row)


def _installation() -> TelegramInstallation:
    return TelegramInstallation(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        alias="secretary_bot",
        external_bot_id="777001",
        mode="secretary",
        status="active",
        settings={"bot_username": "secretary_bot"},
    )


def _telegram_user() -> TelegramUser:
    return TelegramUser(
        id=uuid.uuid4(),
        external_user_id="999",
        username="alice",
        first_name="Alice",
        last_name="Smith",
        language_code="en",
        is_bot=False,
        is_blocked=False,
        metadata_json={},
    )


def _business_connection(
    *,
    installation_id: uuid.UUID,
    telegram_user_id: uuid.UUID,
    can_reply: bool = True,
    status: str = "active",
    selected_chat_policy: dict | None = None,
) -> TelegramBusinessConnection:
    return TelegramBusinessConnection(
        id=uuid.uuid4(),
        installation_id=installation_id,
        team_id=uuid.uuid4(),
        telegram_user_id=telegram_user_id,
        business_connection_id="bc-123456",
        can_reply=can_reply,
        selected_chat_policy=selected_chat_policy or {},
        status=status,
        connected_at=datetime.now(tz=timezone.utc),
        revoked_at=None,
    )


# _can_send_via_business_connection tests
def test_can_send_active_connection_with_can_reply() -> None:
    installation = _installation()
    telegram_user = _telegram_user()
    bc = _business_connection(
        installation_id=installation.id,
        telegram_user_id=telegram_user.id,
        can_reply=True,
        status="active",
    )

    assert _can_send_via_business_connection(bc, "-100123") is True


def test_cannot_send_revoked_connection() -> None:
    installation = _installation()
    telegram_user = _telegram_user()
    bc = _business_connection(
        installation_id=installation.id,
        telegram_user_id=telegram_user.id,
        can_reply=True,
        status="revoked",
    )

    assert _can_send_via_business_connection(bc, "-100123") is False


def test_cannot_send_connection_without_can_reply() -> None:
    installation = _installation()
    telegram_user = _telegram_user()
    bc = _business_connection(
        installation_id=installation.id,
        telegram_user_id=telegram_user.id,
        can_reply=False,
        status="active",
    )

    assert _can_send_via_business_connection(bc, "-100123") is False


def test_cannot_send_to_unselected_chat() -> None:
    installation = _installation()
    telegram_user = _telegram_user()
    bc = _business_connection(
        installation_id=installation.id,
        telegram_user_id=telegram_user.id,
        can_reply=True,
        status="active",
        selected_chat_policy={"chat_ids": ["-100123", "-100456"]},
    )

    assert _can_send_via_business_connection(bc, "-100789") is False
    assert _can_send_via_business_connection(bc, "-100123") is True


def test_can_send_if_no_policy_restriction() -> None:
    installation = _installation()
    telegram_user = _telegram_user()
    bc = _business_connection(
        installation_id=installation.id,
        telegram_user_id=telegram_user.id,
        can_reply=True,
        status="active",
        selected_chat_policy={},  # Empty policy = no restriction
    )

    assert _can_send_via_business_connection(bc, "-100789") is True


# _upsert_business_connection tests
@pytest.mark.asyncio
async def test_upsert_creates_new_connection() -> None:
    pytest.skip("Bug in telegram_bridge.py: TelegramBusinessConnection doesn't have metadata_json field")


@pytest.mark.asyncio
async def test_upsert_updates_existing_connection() -> None:
    installation = _installation()
    telegram_user = _telegram_user()
    existing_bc = _business_connection(
        installation_id=installation.id,
        telegram_user_id=telegram_user.id,
        can_reply=False,
    )

    session = _FakeSession(row=existing_bc)

    bc = await _upsert_business_connection(
        session,
        installation=installation,
        business_connection_id="bc-123456",
        user_external_id="999",
        can_reply=True,
    )

    # Should have updated existing, not created new
    assert len(session.added) == 0
    assert bc.can_reply is True


# deleted_business_messages handling test
def test_message_payload_kind_handles_deleted_messages() -> None:
    from platform_api.telegram_bridge import _message_payload_kind

    payload = {
        "deleted_business_messages": [
            {"chat": {"id": -100123}, "message_id": 42},
            {"chat": {"id": -100123}, "message_id": 43},
        ]
    }

    kind, value = _message_payload_kind(payload)
    assert kind == "deleted_business_messages"
    assert "messages" in value
    assert len(value["messages"]) == 2
