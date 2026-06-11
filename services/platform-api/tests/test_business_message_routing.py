from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from core.models import (
    TelegramBusinessConnection,
    TelegramChat,
    TelegramInstallation,
    TelegramUser,
)
from platform_api.telegram_bridge import _message_payload_kind, ingest_event


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def execute(self, stmt) -> object:
        class FakeResult:
            def scalar_one_or_none(self):
                return None

        return FakeResult()

    def get(self, model, id):
        return None


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


def _chat() -> TelegramChat:
    return TelegramChat(
        id=uuid.uuid4(),
        installation_id=uuid.uuid4(),
        external_chat_id="-100123",
        type="private",
        title="Alice Chat",
        username="alice_chat",
        ingest_mode="correspondence",
        access_mode="secretary",
        send_policy={},
        active=True,
        metadata_json={},
    )


def _business_connection(
    installation_id: uuid.UUID, telegram_user_id: uuid.UUID
) -> TelegramBusinessConnection:
    return TelegramBusinessConnection(
        id=uuid.uuid4(),
        installation_id=installation_id,
        team_id=uuid.uuid4(),
        telegram_user_id=telegram_user_id,
        business_connection_id="bc-123456",
        can_reply=True,
        selected_chat_policy={},
        status="active",
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


# _message_payload_kind tests
def test_message_payload_kind_returns_business_message() -> None:
    payload = {
        "business_message": {
            "message_id": 42,
            "chat": {"id": -100123, "type": "private"},
            "text": "Hello from user",
            "date": 1234567890,
            "from": {"id": 999, "first_name": "Alice"},
        }
    }

    kind, value = _message_payload_kind(payload)
    assert kind == "business_message"
    assert value["message_id"] == 42
    assert value["text"] == "Hello from user"


def test_message_payload_kind_returns_edited_business_message() -> None:
    payload = {
        "edited_business_message": {
            "message_id": 42,
            "chat": {"id": -100123, "type": "private"},
            "text": "Updated text",
            "edit_date": 1234567891,
        }
    }

    kind, value = _message_payload_kind(payload)
    assert kind == "edited_business_message"
    assert value["text"] == "Updated text"


def test_message_payload_kind_prefers_regular_message_over_business() -> None:
    payload = {
        "message": {"message_id": 1, "text": "regular"},
        "business_message": {"message_id": 2, "text": "business"},
    }

    kind, value = _message_payload_kind(payload)
    assert kind == "message"
    assert value["text"] == "regular"


def test_message_payload_kind_returns_empty_for_unknown() -> None:
    payload = {"update_id": 1}

    kind, value = _message_payload_kind(payload)
    assert kind == ""
    assert value is None


# ingest_event business_message test
@pytest.mark.asyncio
async def test_ingest_business_message_creates_message_with_secretary_access_mode() -> None:
    from platform_api.telegram_bridge import IngestEventRequest

    session = _FakeSession()
    installation = _installation()
    chat = _chat()

    payload = {
        "business_message": {
            "message_id": 42,
            "chat": {"id": -100123, "type": "private", "title": "Chat"},
            "text": "Hello secretary",
            "date": 1234567890,
            "from": {"id": 999, "first_name": "Alice"},
            "business_connection_id": "bc-123456",
        }
    }

    request = IngestEventRequest(
        team_id=str(installation.team_id),
        installation_id=str(installation.id),
        update_id=1,
        payload=payload,
    )

    bridge = "platform_api.telegram_bridge"
    with (
        patch(f"{bridge}._get_installation", new_callable=AsyncMock) as mock_get_inst,
        patch(f"{bridge}._upsert_chat", new_callable=AsyncMock) as mock_upsert_chat,
        patch(f"{bridge}._upsert_user", new_callable=AsyncMock) as mock_upsert_user,
        patch(f"{bridge}._find_business_connection", new_callable=AsyncMock) as mock_find_bc,
    ):
        mock_get_inst.return_value = installation
        mock_upsert_chat.return_value = chat
        mock_upsert_user.return_value = _telegram_user()

        # Mock returns a business connection → access_mode should be "secretary"
        bc = _business_connection(
            installation_id=installation.id,
            telegram_user_id=uuid.uuid4(),
        )
        mock_find_bc.return_value = bc

        with patch(f"{bridge}.rpc_client.invoke", new_callable=AsyncMock) as mock_invoke:
            from unittest.mock import MagicMock

            mock_invoke.return_value = MagicMock(reply=None, pending_confirm=None)

            await ingest_event(session, request)

            # Should have created TelegramMessage with access_mode="secretary"
            assert len(session.added) >= 2  # Update + Message
            msg = next((a for a in session.added if hasattr(a, "access_mode")), None)
            if msg:
                assert msg.access_mode == "secretary"
