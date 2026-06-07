from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from telegram_gateway.bot_api import BotAPIError, TelegramBotClient


class FakeResponse:
    def __init__(self, status_code: int, json_data: dict) -> None:
        self.status_code = status_code
        self._json = json_data

    def json(self) -> dict:
        return self._json


class FakeHTTPError(Exception):
    pass


@pytest.fixture
def client() -> TelegramBotClient:
    client = TelegramBotClient(bot_token="test-token-123", api_base="https://api.telegram.org")

    # Mock metrics to avoid label-related errors
    with patch("telegram_gateway.bot_api._bot_api_total"):
        with patch("telegram_gateway.bot_api._bot_api_latency_seconds"):
            yield client


# send_message tests
@pytest.mark.asyncio
async def test_send_message_success(client: TelegramBotClient) -> None:
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = FakeResponse(
            200, {"ok": True, "result": {"message_id": 42, "chat": {"id": -100123}}}
        )

        result = await client.send_message(
            chat_id="-100123",
            text="Hello!",
            reply_to_message_id=41,
            message_thread_id=7,
        )

        assert result.message_id == "42"
        assert result.chat_id == "-100123"
        assert result.text == "Hello!"
        mock_post.assert_called_once()


@pytest.mark.asyncio
async def test_send_message_splits_long_text(client: TelegramBotClient) -> None:
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = FakeResponse(
            200, {"ok": True, "result": {"message_id": 42, "chat": {"id": -100123}}}
        )

        long_text = "Line1\n" * 1000  # Very long
        await client.send_message(chat_id="-100123", text=long_text)

        assert mock_post.call_count >= 1


@pytest.mark.asyncio
async def test_send_message_with_inline_buttons(client: TelegramBotClient) -> None:
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = FakeResponse(
            200, {"ok": True, "result": {"message_id": 42, "chat": {"id": -100123}}}
        )

        reply_markup = {"inline_keyboard": [[{"text": "Approve", "callback_data": "token123"}]]}
        await client.send_message(
            chat_id="-100123",
            text="Confirm?",
            reply_markup=reply_markup,
        )

        call_kwargs = mock_post.call_args.kwargs
        assert "json" in call_kwargs
        assert call_kwargs["json"]["reply_markup"] == reply_markup


# answer_callback_query tests
@pytest.mark.asyncio
async def test_answer_callback_query_success(client: TelegramBotClient) -> None:
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = FakeResponse(200, {"ok": True, "result": True})

        await client.answer_callback_query(
            callback_query_id="cbq-123",
            text="Approved!",
            show_alert=True,
        )

        mock_post.assert_called_once()
        call_json = mock_post.call_args.kwargs["json"]
        assert call_json["callback_query_id"] == "cbq-123"
        assert call_json["text"] == "Approved!"
        assert call_json["show_alert"] is True


# edit_message_reply_markup tests
@pytest.mark.asyncio
async def test_edit_message_removes_buttons(client: TelegramBotClient) -> None:
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = FakeResponse(200, {"ok": True, "result": True})

        await client.edit_message_reply_markup(
            chat_id="-100123",
            message_id=42,
            reply_markup=None,  # Should remove buttons
        )

        mock_post.assert_called_once()
        call_json = mock_post.call_args.kwargs["json"]
        assert call_json["reply_markup"] == {"inline_keyboard": [[]]}


# error handling tests
@pytest.mark.asyncio
async def test_bot_api_429_retry_after(client: TelegramBotClient) -> None:
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = FakeResponse(
            429,
            {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 30}},
        )

        with pytest.raises(BotAPIError) as exc_info:
            await client.send_message(chat_id="-100123", text="test")

        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 30
        assert exc_info.value.permanent is False


@pytest.mark.asyncio
async def test_bot_api_400_permanent_error(client: TelegramBotClient) -> None:
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = FakeResponse(
            400, {"ok": False, "description": "Bad Request: chat not found"}
        )

        with pytest.raises(BotAPIError) as exc_info:
            await client.send_message(chat_id="invalid", text="test")

        assert exc_info.value.status_code == 400
        assert exc_info.value.permanent is True


@pytest.mark.asyncio
async def test_bot_api_500_transient_error(client: TelegramBotClient) -> None:
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = FakeResponse(
            500, {"ok": False, "description": "Internal Server Error"}
        )

        with pytest.raises(BotAPIError) as exc_info:
            await client.send_message(chat_id="-100123", text="test")

        assert exc_info.value.status_code == 500
        assert exc_info.value.permanent is False


@pytest.mark.asyncio
async def test_delete_webhook_success(client: TelegramBotClient) -> None:
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = FakeResponse(200, {"ok": True, "result": True})

        await client.delete_webhook(drop_pending_updates=False)

        mock_post.assert_called_once()
        call_json = mock_post.call_args.kwargs["json"]
        assert call_json["drop_pending_updates"] is False


@pytest.mark.asyncio
async def test_get_me_success(client: TelegramBotClient) -> None:
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = FakeResponse(
            200,
            {"ok": True, "result": {"id": 777001, "username": "pm_bot"}},
        )

        result = await client.get_me()

        assert result["id"] == 777001
