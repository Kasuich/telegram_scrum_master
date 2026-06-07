from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from core.models import TelegramChat, TelegramInstallation, TelegramMessage, TelegramUser
from core.react import AgentResult, PendingConfirm
from platform_api.telegram_bridge import (
    _route_inbound_message,
    _should_route_message,
)


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


def _installation(**overrides: object) -> TelegramInstallation:
    data = {
        "id": uuid.uuid4(),
        "team_id": uuid.uuid4(),
        "alias": "pm_bot",
        "external_bot_id": "777000",
        "mode": "workspace_bot",
        "status": "active",
        "settings": {"bot_username": "pm_bot"},
    }
    data.update(overrides)
    return TelegramInstallation(**data)


def _chat(**overrides: object) -> TelegramChat:
    data = {
        "id": uuid.uuid4(),
        "installation_id": uuid.uuid4(),
        "external_chat_id": "-100123",
        "type": "group",
        "title": "Team",
        "username": None,
        "ingest_mode": "mentions",
        "access_mode": "workspace_bot",
        "send_policy": {},
        "active": True,
        "metadata_json": {},
    }
    data.update(overrides)
    return TelegramChat(**data)


def _message(**overrides: object) -> TelegramMessage:
    data = {
        "id": uuid.uuid4(),
        "team_id": uuid.uuid4(),
        "installation_id": uuid.uuid4(),
        "chat_id": uuid.uuid4(),
        "telegram_user_id": uuid.uuid4(),
        "business_connection_ref_id": None,
        "raw_update_id": uuid.uuid4(),
        "direction": "inbound",
        "access_mode": "workspace_bot",
        "external_chat_id": "-100123",
        "external_message_id": "42",
        "external_thread_id": "7",
        "reply_to_external_message_id": None,
        "message_kind": "message",
        "import_source": None,
        "text": "status?",
        "caption": None,
        "sent_at": None,
        "edited_at": None,
        "deleted_at": None,
        "media_json": {},
        "metadata_json": {},
    }
    data.update(overrides)
    return TelegramMessage(**data)


def _telegram_user(**overrides: object) -> TelegramUser:
    data = {
        "id": uuid.uuid4(),
        "external_user_id": "991",
        "username": "ivan",
        "first_name": "Ivan",
        "last_name": "Petrov",
        "language_code": "ru",
        "is_bot": False,
        "is_blocked": False,
        "metadata_json": {},
    }
    data.update(overrides)
    return TelegramUser(**data)


def test_should_route_private_direct_message() -> None:
    installation = _installation()
    chat = _chat(type="private", ingest_mode="direct")

    assert _should_route_message(installation, chat, {"text": "hello"}) is True
    assert (
        _should_route_message(
            installation,
            _chat(type="group", ingest_mode="direct"),
            {"text": "hello"},
        )
        is False
    )


@pytest.mark.asyncio
async def test_upsert_new_private_chat_defaults_to_direct() -> None:
    installation = _installation()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()

    from platform_api.telegram_bridge import _upsert_chat

    chat = await _upsert_chat(
        session,
        installation,
        {"id": 991, "type": "private", "first_name": "Ivan"},
    )

    assert chat.ingest_mode == "direct"
    session.add.assert_called_once_with(chat)


@pytest.mark.asyncio
async def test_upsert_new_group_uses_installation_ingest_mode() -> None:
    installation = _installation(
        settings={
            "bot_username": "pm_bot",
            "group_ingest_mode": "mentions",
        }
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()

    from platform_api.telegram_bridge import _upsert_chat

    chat = await _upsert_chat(
        session,
        installation,
        {"id": -100123, "type": "supergroup", "title": "Team"},
    )

    assert chat.ingest_mode == "mentions"


def test_should_route_mentions_and_replies_to_bot() -> None:
    installation = _installation()
    chat = _chat(ingest_mode="mentions")

    mention_payload = {
        "text": "@pm_bot status?",
        "entities": [{"type": "mention", "offset": 0, "length": 7}],
    }
    reply_payload = {
        "text": "status?",
        "reply_to_message": {
            "message_id": 41,
            "from": {"id": 777000, "is_bot": True},
        },
    }

    assert _should_route_message(installation, chat, mention_payload) is True
    assert _should_route_message(installation, chat, reply_payload) is True
    assert (
        _should_route_message(
            installation,
            _chat(ingest_mode="archive_only"),
            mention_payload,
        )
        is False
    )


@pytest.mark.asyncio
async def test_route_inbound_message_creates_agent_reply_outbox() -> None:
    session = _FakeSession()
    installation = _installation()
    chat = _chat()
    message = _message()
    telegram_user = _telegram_user()
    payload = {"text": "/status", "from": {"first_name": "Ivan"}}

    with patch(
        "platform_api.telegram_bridge.rpc_client.invoke",
        AsyncMock(return_value=AgentResult(reply="All good", session_id="s1")),
    ) as invoke:
        routing = await _route_inbound_message(
            session,
            installation=installation,
            chat=chat,
            message=message,
            telegram_user=telegram_user,
            message_payload=payload,
        )

    assert routing is not None
    assert routing["reply"] == "All good"
    assert routing["pending_confirm_id"] is None
    invoke.assert_awaited_once()
    assert invoke.await_args.args[0] == "pm_agent"
    assert invoke.await_args.args[1] == "/status"
    assert invoke.await_args.args[2].startswith(f"telegram:{installation.id}:")
    context = invoke.await_args.kwargs["context"]
    assert context.chat_id == "-100123"
    assert context.thread_id == "7"
    assert context.actor_external_id == "991"
    assert len(session.added) == 1
    outbox = session.added[0]
    assert outbox.category == "agent_reply"
    assert outbox.target_chat_id == "-100123"
    assert outbox.target_user_id is None
    assert outbox.payload["text"] == "All good"
    assert outbox.payload["reply_to_message_id"] == "42"


@pytest.mark.asyncio
async def test_route_inbound_message_creates_confirmation_outbox() -> None:
    session = _FakeSession()
    installation = _installation()
    chat = _chat()
    message = _message()
    telegram_user = _telegram_user()
    payload = {
        "text": "@pm_bot create bug",
        "entities": [{"type": "mention", "offset": 0, "length": 7}],
    }
    result = AgentResult(
        pending_confirm=PendingConfirm(
            confirm_id=str(uuid.uuid4()),
            tool_name="tracker_create_issue",
            tool_args={"summary": "bug"},
            risk="medium",
            prompt="Create task?",
        ),
        session_id="s1",
    )

    with patch("platform_api.telegram_bridge.rpc_client.invoke", AsyncMock(return_value=result)):
        routing = await _route_inbound_message(
            session,
            installation=installation,
            chat=chat,
            message=message,
            telegram_user=telegram_user,
            message_payload=payload,
        )

    assert routing is not None
    assert routing["reply"] is None
    assert len(routing["pending_confirm_id"]) == 36
    assert len(session.added) == 3
    outbox = next(
        obj for obj in session.added if hasattr(obj, "payload") and hasattr(obj, "category")
    )
    assert outbox.category == "confirmation"
    assert outbox.payload["text"] == "Create task?"
    assert len(outbox.payload["metadata"]["confirm_id"]) == 36
    assert "reply_markup" in outbox.payload
