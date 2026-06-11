from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BotAPIError(Exception):
    status_code: int
    retry_after: int | None = None
    permanent: bool = False
    description: str = ""


@dataclass(frozen=True, slots=True)
class SendResult:
    message_id: str
    chat_id: str
    text: str
    sent_at: datetime


class TelegramBotClient:
    MAX_MESSAGE_LENGTH = 4096

    def __init__(
        self,
        bot_token: str,
        api_base: str = "https://api.telegram.org",
    ) -> None:
        self.bot_token = bot_token
        self.api_base = api_base.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    def _get_url(self, method: str) -> str:
        return f"{self.api_base}/bot{self.bot_token}/{method}"

    async def _request(
        self,
        method: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        start = time.monotonic()
        elapsed = 0.0
        status = "error"

        try:
            response = await self._client.post(
                self._get_url(method),
                json=payload,
            )
            elapsed = time.monotonic() - start

            result = response.json()
            if not result.get("ok", False):
                self._handle_error(result, response.status_code)

            status = "success"
            result = result.get("result", {})
            if isinstance(result, bool):
                return {"success": result}
            return dict(result)

        except httpx.HTTPStatusError as exc:
            elapsed = time.monotonic() - start
            self._handle_error(
                {"description": exc.response.text},
                exc.response.status_code,
            )
        except httpx.HTTPError as exc:
            elapsed = time.monotonic() - start
            raise BotAPIError(
                status_code=500,
                description=str(exc),
                permanent=False,
            ) from exc
        finally:
            _bot_api_total.labels(method=method, status=status).inc()
            _bot_api_latency_seconds.labels(method=method).observe(elapsed)

    def _handle_error(self, result: dict[str, object], status_code: int) -> None:
        description = str(result.get("description", ""))
        logger.warning(
            "Bot API error: status=%d description=%s",
            status_code,
            description,
        )

        if status_code == 429:
            retry_after = int(result.get("parameters", {}).get("retry_after", 0))
            raise BotAPIError(
                status_code=status_code,
                retry_after=retry_after,
                permanent=False,
                description=description,
            )

        permanent = 400 <= status_code < 500
        raise BotAPIError(
            status_code=status_code,
            permanent=permanent,
            description=description,
        )

    def _split_message(self, text: str) -> list[str]:
        if len(text) <= self.MAX_MESSAGE_LENGTH:
            return [text]

        parts = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 <= self.MAX_MESSAGE_LENGTH:
                current += ("\n" if current else "") + line
            else:
                if current:
                    parts.append(current)
                current = line
        if current:
            parts.append(current)
        return parts

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        reply_to_message_id: str | int | None = None,
        message_thread_id: int | None = None,
        reply_markup: dict[str, object] | None = None,
        parse_mode: str | None = None,
    ) -> SendResult:
        parts = self._split_message(text)
        result_part = None

        for i, part in enumerate(parts):
            payload: dict[str, object] = {
                "chat_id": chat_id,
                "text": part,
            }

            if reply_to_message_id:
                payload["reply_to_message_id"] = reply_to_message_id

            if message_thread_id:
                payload["message_thread_id"] = message_thread_id

            if reply_markup:
                payload["reply_markup"] = reply_markup

            if parse_mode:
                payload["parse_mode"] = parse_mode

            result = await self._request("sendMessage", payload)
            if i == len(parts) - 1:
                result_part = result

        if not result_part:
            raise BotAPIError(status_code=500, description="No result received")

        return SendResult(
            message_id=str(result_part.get("message_id")),
            chat_id=str(result_part.get("chat", {}).get("id", chat_id)),
            text=text,
            sent_at=datetime.now(tz=timezone.utc),
        )

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        payload: dict[str, object] = {
            "callback_query_id": callback_query_id,
        }

        if text:
            payload["text"] = text

        payload["show_alert"] = show_alert

        await self._request("answerCallbackQuery", payload)

    async def edit_message_reply_markup(
        self,
        chat_id: str | int,
        message_id: str | int,
        reply_markup: dict[str, object] | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "message_id": message_id,
        }

        if reply_markup is None:
            payload["reply_markup"] = {"inline_keyboard": [[]]}
        else:
            payload["reply_markup"] = reply_markup

        await self._request("editMessageReplyMarkup", payload)

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 30,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, object]]:
        payload: dict[str, object] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates

        response = await self._client.post(
            self._get_url("getUpdates"),
            json=payload,
            timeout=timeout + 5,
        )
        result = response.json()
        if not result.get("ok", False):
            self._handle_error(result, response.status_code)
        updates = result.get("result", [])
        if not isinstance(updates, list):
            return []
        return [dict(item) for item in updates if isinstance(item, dict)]

    async def get_me(self) -> dict[str, object]:
        return await self._request("getMe", {})

    async def set_webhook(
        self,
        *,
        url: str,
        secret_token: str,
    ) -> None:
        await self._request(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret_token,
            },
        )

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        await self._request(
            "deleteWebhook",
            {"drop_pending_updates": drop_pending_updates},
        )


# ── Metrics ─────────────────────────────────────────────────────────────────────

_bot_api_total = Counter(
    "telegram_gateway_bot_api_total",
    "Total Telegram Bot API requests",
    ["method", "status"],
)

_bot_api_latency_seconds = Histogram(
    "telegram_gateway_bot_api_latency_seconds",
    "Telegram Bot API request latency in seconds",
    ["method"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0],
)


__all__ = [
    "BotAPIError",
    "SendResult",
    "TelegramBotClient",
]
