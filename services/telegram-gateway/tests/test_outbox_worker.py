from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram_gateway.bot_api import BotAPIError, SendResult, TelegramBotClient
from telegram_gateway.bridge import LeaseItem, LeaseResponse, MainBridgeClient
from telegram_gateway.runtime import GatewayRuntime
from telegram_gateway.settings import GatewaySettings
from telegram_gateway.spool import GatewaySpool


def make_runtime(tmp_path: Path) -> GatewayRuntime:
    settings = GatewaySettings(
        bot_token="bot-token",
        webhook_secret="hook-secret",
        main_bridge_url="https://main.example",
        bridge_key_id="key-1",
        bridge_key_secret="secret-1",
        spool_path=tmp_path / "spool.db",
    )

    bridge = MagicMock(spec=MainBridgeClient)
    bridge.lease_outbox = AsyncMock()
    bridge.ack_outbox = AsyncMock()

    bot_client = MagicMock(spec=TelegramBotClient)
    bot_client.send_message = AsyncMock()
    bot_client.answer_callback_query = AsyncMock()
    bot_client.edit_message_reply_markup = AsyncMock()

    return GatewayRuntime(
        settings=settings,
        spool=GatewaySpool(settings.spool_path),
        bridge=bridge,
        bot_client=bot_client,
        auto_start_workers=False,
    )


@pytest.mark.asyncio
async def test_deliver_once_leases_and_delivers_messages(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    lease_response = LeaseResponse(items=[
        LeaseItem(
            delivery_id="delivery-1",
            team_id="team-1",
            installation_id="inst-1",
            category="agent_reply",
            target_chat_id="-100123",
            target_user_id="991",
            payload={"method": "sendMessage", "text": "Hello!", "reply_to_message_id": "42"},
            business_connection_id=None,
            lease_expires_at=datetime.now(tz=timezone.utc),
        )
    ])
    runtime.bridge.lease_outbox = AsyncMock(return_value=lease_response)
    runtime.bot_client.send_message = AsyncMock(return_value=SendResult(
        message_id="43", chat_id="-100123", text="Hello!", sent_at=datetime.now(tz=timezone.utc)
    ))

    delivered = await runtime.deliver_once()

    assert delivered == 1
    runtime.bridge.lease_outbox.assert_awaited_once()
    runtime.bot_client.send_message.assert_awaited_once()
    assert runtime.bot_client.send_message.await_args.kwargs["chat_id"] == "-100123"
    runtime.bridge.ack_outbox.assert_awaited_once_with(
        delivery_id="delivery-1",
        status="sent",
        provider_message_id="43",
        last_error=None,
        retry_after_seconds=None,
    )


@pytest.mark.asyncio
async def test_deliver_once_handles_429_retry(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    lease_response = LeaseResponse(items=[
        LeaseItem(
            delivery_id="delivery-1",
            team_id="team-1",
            installation_id="inst-1",
            category="agent_reply",
            target_chat_id="-100123",
            target_user_id=None,
            payload={"method": "sendMessage", "text": "Hello!"},
            business_connection_id=None,
            lease_expires_at=datetime.now(tz=timezone.utc),
        )
    ])
    runtime.bridge.lease_outbox = AsyncMock(return_value=lease_response)
    runtime.bot_client.send_message = AsyncMock(side_effect=BotAPIError(
        status_code=429, retry_after=30, permanent=False
    ))

    delivered = await runtime.deliver_once()

    assert delivered == 0
    runtime.bridge.ack_outbox.assert_awaited_once()
    ack_call = runtime.bridge.ack_outbox.call_args
    assert ack_call.kwargs["status"] == "retry"
    assert ack_call.kwargs["retry_after_seconds"] == 30


@pytest.mark.asyncio
async def test_deliver_once_handles_permanent_error_dead_letter(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    lease_response = LeaseResponse(items=[
        LeaseItem(
            delivery_id="delivery-1",
            team_id="team-1",
            installation_id="inst-1",
            category="agent_reply",
            target_chat_id="invalid",
            target_user_id=None,
            payload={"method": "sendMessage", "text": "Hello!"},
            business_connection_id=None,
            lease_expires_at=datetime.now(tz=timezone.utc),
        )
    ])
    runtime.bridge.lease_outbox = AsyncMock(return_value=lease_response)
    runtime.bot_client.send_message = AsyncMock(side_effect=BotAPIError(
        status_code=400, permanent=True
    ))

    delivered = await runtime.deliver_once()

    assert delivered == 0
    ack_call = runtime.bridge.ack_outbox.call_args
    assert ack_call.kwargs["status"] == "dead_letter"


@pytest.mark.asyncio
async def test_deliver_once_empty_when_no_items(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.bridge.lease_outbox = AsyncMock(return_value=LeaseResponse(items=[]))

    delivered = await runtime.deliver_once()

    assert delivered == 0
    runtime.bot_client.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_once_callback_answer(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    lease_response = LeaseResponse(items=[
        LeaseItem(
            delivery_id="delivery-cbq",
            team_id="team-1",
            installation_id="inst-1",
            category="confirmation",
            target_chat_id="-100123",
            target_user_id=None,
            payload={
                "method": "answerCallbackQuery",
                "callback_query_id": "cbq-1",
                "text": "Approved",
                "show_alert": True,
            },
            business_connection_id=None,
            lease_expires_at=datetime.now(tz=timezone.utc),
        )
    ])
    runtime.bridge.lease_outbox = AsyncMock(return_value=lease_response)
    runtime.bot_client.answer_callback_query = AsyncMock()

    delivered = await runtime.deliver_once()

    assert delivered == 1
    runtime.bot_client.answer_callback_query.assert_awaited_once_with(
        callback_query_id="cbq-1",
        text="Approved",
        show_alert=True,
    )


@pytest.mark.asyncio
async def test_deliver_once_edit_message_markup(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    lease_response = LeaseResponse(items=[
        LeaseItem(
            delivery_id="delivery-edit",
            team_id="team-1",
            installation_id="inst-1",
            category="confirmation",
            target_chat_id="-100123",
            target_user_id=None,
            payload={
                "method": "editMessageReplyMarkup",
                "message_id": "42",
                "reply_markup": {"inline_keyboard": []},
            },
            business_connection_id=None,
            lease_expires_at=datetime.now(tz=timezone.utc),
        )
    ])
    runtime.bridge.lease_outbox = AsyncMock(return_value=lease_response)
    runtime.bot_client.edit_message_reply_markup = AsyncMock()

    delivered = await runtime.deliver_once()

    assert delivered == 1
    runtime.bot_client.edit_message_reply_markup.assert_awaited_once_with(
        chat_id="-100123",
        message_id="42",
        reply_markup={"inline_keyboard": []},
    )


@pytest.mark.asyncio
async def test_deliver_once_no_bot_client_returns_zero(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.bot_client = None

    delivered = await runtime.deliver_once()

    assert delivered == 0


@pytest.mark.asyncio
async def test_poll_updates_once_stores_updates_in_polling_mode(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.settings = GatewaySettings(
        bot_token=runtime.settings.bot_token,
        webhook_secret=runtime.settings.webhook_secret,
        main_bridge_url=runtime.settings.main_bridge_url,
        bridge_key_id=runtime.settings.bridge_key_id,
        bridge_key_secret=runtime.settings.bridge_key_secret,
        transport_mode="polling",
        spool_path=runtime.settings.spool_path,
    )
    runtime.bot_client.get_updates = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {"update_id": 101, "message": {"message_id": 1, "text": "hello"}},
            {"update_id": 102, "message": {"message_id": 2, "text": "world"}},
        ]
    )

    accepted = await runtime.poll_updates_once(timeout=1)

    assert accepted == 2
    assert runtime.spool.depth() == 2
    assert runtime.next_update_offset == 103


@pytest.mark.asyncio
async def test_sync_transport_mode_deletes_webhook_for_polling(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.settings = GatewaySettings(
        bot_token=runtime.settings.bot_token,
        webhook_secret=runtime.settings.webhook_secret,
        main_bridge_url=runtime.settings.main_bridge_url,
        bridge_key_id=runtime.settings.bridge_key_id,
        bridge_key_secret=runtime.settings.bridge_key_secret,
        transport_mode="polling",
        spool_path=runtime.settings.spool_path,
    )
    runtime.bot_client.delete_webhook = AsyncMock()  # type: ignore[method-assign]

    await runtime.sync_transport_mode()

    runtime.bot_client.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
    runtime.bridge.lease_outbox.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_once_no_bridge_returns_zero(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.bridge = None

    delivered = await runtime.deliver_once()

    assert delivered == 0
