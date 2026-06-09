from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from core.invocation import format_actor_prefixed_message
from core.models import TelegramChat, TelegramInstallation, TelegramMessage, TelegramUser
from core.react import AgentResult, PendingConfirm
from platform_api.telegram_bridge import (
    _build_invocation_context,
    _extract_telemost_url,
    _route_inbound_message,
    _should_route_message,
    _strip_bot_mention,
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


def _confirmed_identity():
    membership = MagicMock()
    membership.user_id = uuid.uuid4()
    membership.tracker_login = "ivan.petrov"
    membership.default_board_id = "3"
    return MagicMock(), membership


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
async def test_strip_bot_mention_removes_username_entity() -> None:
    installation = _installation()
    payload = {
        "text": "@pm_bot создай задачу urok",
        "entities": [{"type": "mention", "offset": 0, "length": 7}],
    }
    assert _strip_bot_mention(installation, payload) == "создай задачу urok"


def test_format_actor_prefixed_message_for_group_mention() -> None:
    installation = _installation()
    chat = _chat()
    message = _message()
    user = _telegram_user(first_name="Roman", last_name="Shinkarenko", username="romansh")
    payload = {
        "text": "@pm_bot создай задачу urok",
        "entities": [{"type": "mention", "offset": 0, "length": 7}],
    }
    ctx = _build_invocation_context(installation, chat, message, user, payload)
    assert format_actor_prefixed_message(ctx.raw_text_without_mention or "", ctx) == (
        "Roman Shinkarenko: создай задачу urok"
    )


def test_build_invocation_context_fills_telegram_fields() -> None:
    installation = _installation()
    chat = _chat()
    message = _message()
    user = _telegram_user(first_name="Roman", last_name="Shinkarenko", username="romansh")
    payload = {
        "text": "@pm_bot создай задачу",
        "entities": [{"type": "mention", "offset": 0, "length": 7}],
        "from": {
            "id": 991,
            "first_name": "Roman",
            "last_name": "Shinkarenko",
            "username": "romansh",
        },
    }
    ctx = _build_invocation_context(installation, chat, message, user, payload)
    assert ctx.channel == "telegram"
    assert ctx.actor_display_name == "Roman Shinkarenko"
    assert ctx.actor_username == "romansh"
    assert ctx.raw_text_without_mention == "создай задачу"
    assert ctx.is_bot_mentioned is True
    assert ctx.chat_title == "Team"
    assert ctx.metadata["chat_type"] == "group"


def test_extract_telemost_url_from_plain_message() -> None:
    url = "https://telemost.yandex.ru/j/12345678901234567"
    assert _extract_telemost_url(url) == url
    assert _extract_telemost_url(f"заходи {url}.") == url


def test_extract_telemost_url_ignores_non_links() -> None:
    assert _extract_telemost_url("создай задачу") is None


@pytest.mark.asyncio
async def test_unauthorized_group_mention_starts_onboarding() -> None:
    session = _FakeSession()
    installation = _installation()
    chat = _chat()
    message = _message()
    telegram_user = _telegram_user()
    payload = {
        "text": "@pm_bot status?",
        "entities": [{"type": "mention", "offset": 0, "length": 7}],
    }
    onboarding_id = uuid.uuid4()

    with (
        patch(
            "platform_api.telegram_bridge.get_confirmed_membership",
            AsyncMock(return_value=None),
        ),
        patch(
            "platform_api.telegram_bridge.start_onboarding",
            AsyncMock(return_value=SimpleNamespace(id=onboarding_id)),
        ) as start,
        patch("platform_api.telegram_bridge.rpc_client.invoke", AsyncMock()) as invoke,
    ):
        routing = await _route_inbound_message(
            session,
            installation=installation,
            chat=chat,
            message=message,
            telegram_user=telegram_user,
            message_payload=payload,
        )

    invoke.assert_not_awaited()
    start.assert_awaited_once()
    assert routing == {
        "authorization": "pending",
        "onboarding_id": str(onboarding_id),
    }


@pytest.mark.asyncio
async def test_route_inbound_message_short_circuits_telemost_link() -> None:
    session = _FakeSession()
    installation = _installation()
    chat = _chat(type="private", ingest_mode="direct")
    message = _message()
    telegram_user = _telegram_user()
    telemost_url = "https://telemost.yandex.ru/j/12345678901234567"
    payload = {"text": telemost_url, "from": {"first_name": "Ivan"}}
    meeting_reply = "🤖 Иду на встречу и включаю запись."

    with (
        patch(
            "platform_api.telegram_bridge.rpc_client.invoke",
            AsyncMock(),
        ) as invoke,
        patch(
            "platform_api.telegram_bridge._schedule_meeting_capture",
            AsyncMock(return_value=meeting_reply),
        ) as schedule,
        patch(
            "platform_api.telegram_bridge.get_confirmed_membership",
            AsyncMock(return_value=_confirmed_identity()),
        ),
    ):
        routing = await _route_inbound_message(
            session,
            installation=installation,
            chat=chat,
            message=message,
            telegram_user=telegram_user,
            message_payload=payload,
        )

    invoke.assert_not_awaited()
    schedule.assert_awaited_once_with(telemost_url, chat.external_chat_id)
    assert routing is not None
    assert "Иду на встречу" in routing["reply"]
    assert len(session.added) == 1
    assert session.added[0].payload["text"] == meeting_reply


@pytest.mark.asyncio
async def test_route_private_standup_response_short_circuits_agent() -> None:
    session = _FakeSession()
    installation = _installation()
    chat = _chat(type="private", ingest_mode="direct", external_chat_id="991")
    message = _message(external_chat_id="991")
    telegram_user = _telegram_user()
    payload = {"text": "задача 1 закрыта", "from": {"first_name": "Ivan"}}

    with (
        patch(
            "platform_api.telegram_bridge.rpc_client.invoke",
            AsyncMock(),
        ) as invoke,
        patch(
            "platform_api.telegram_bridge.handle_standup_response",
            AsyncMock(return_value="Принял статус:\n- TEST-1: закрыл"),
        ) as handle,
        patch(
            "platform_api.telegram_bridge.get_confirmed_membership",
            AsyncMock(return_value=_confirmed_identity()),
        ),
    ):
        routing = await _route_inbound_message(
            session,
            installation=installation,
            chat=chat,
            message=message,
            telegram_user=telegram_user,
            message_payload=payload,
        )

    invoke.assert_not_awaited()
    handle.assert_awaited_once()
    assert routing is not None
    assert routing["standup_poll"] == "handled"
    assert session.added[0].target_chat_id == "991"
    assert session.added[0].payload["text"].startswith("Принял статус")


@pytest.mark.asyncio
async def test_route_inbound_message_creates_agent_reply_outbox() -> None:
    session = _FakeSession()
    installation = _installation()
    chat = _chat()
    message = _message()
    telegram_user = _telegram_user()
    payload = {"text": "/status", "from": {"first_name": "Ivan"}}

    with (
        patch(
            "platform_api.telegram_bridge.rpc_client.invoke",
            AsyncMock(return_value=AgentResult(reply="All good", session_id="s1")),
        ) as invoke,
        patch(
            "platform_api.telegram_bridge.get_confirmed_membership",
            AsyncMock(return_value=_confirmed_identity()),
        ),
    ):
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
    assert invoke.await_args.args[1] == "Ivan Petrov: /status"
    assert invoke.await_args.kwargs["context"].actor_display_name == "Ivan Petrov"
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

    with (
        patch("platform_api.telegram_bridge.rpc_client.invoke", AsyncMock(return_value=result)),
        patch(
            "platform_api.telegram_bridge.get_confirmed_membership",
            AsyncMock(return_value=_confirmed_identity()),
        ),
    ):
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
