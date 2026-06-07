from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from prometheus_client import Counter, Gauge, Histogram

from telegram_gateway.bot_api import BotAPIError, TelegramBotClient
from telegram_gateway.bridge import (
    BridgeRequestError,
    LeaseItem,
    MainBridgeClient,
    bridge_error_is_permanent,
)
from telegram_gateway.settings import GatewaySettings
from telegram_gateway.spool import GatewaySpool

WEBHOOK_TOTAL = Counter("telegram_gateway_webhook_total", "Webhook requests received", ["status"])
SPOOL_DEPTH = Gauge("telegram_gateway_spool_depth", "Queued inbound updates")
FORWARD_TOTAL = Counter("telegram_gateway_forward_total", "Forwarded inbound updates", ["status"])
HEARTBEAT_TOTAL = Counter("telegram_gateway_heartbeat_total", "Heartbeat requests sent", ["status"])
FORWARD_LATENCY = Histogram(
    "telegram_gateway_forward_latency_seconds",
    "Time spent forwarding one update",
)
DELIVER_TOTAL = Counter("telegram_gateway_deliver_total", "Outbound deliveries", ["status"])
DELIVER_LATENCY = Histogram(
    "telegram_gateway_deliver_latency_seconds",
    "Time to deliver one item",
)


@dataclass(slots=True)
class GatewayRuntime:
    settings: GatewaySettings
    spool: GatewaySpool
    bridge: MainBridgeClient | None
    bot_client: TelegramBotClient | None = None
    auto_start_workers: bool = True
    next_update_offset: int | None = None
    installation_id: str | None = None
    team_id: str | None = None

    def _now(self) -> datetime:
        return datetime.now(tz=timezone.utc)

    def record_webhook(self, status: str) -> None:
        WEBHOOK_TOTAL.labels(status=status).inc()
        SPOOL_DEPTH.set(self.spool.depth())

    def record_depth(self) -> None:
        SPOOL_DEPTH.set(self.spool.depth())

    async def sync_transport_mode(self) -> None:
        if self.bot_client is None:
            return
        if self.settings.transport_mode == "polling":
            await self.bot_client.delete_webhook(drop_pending_updates=False)

    async def resolve_installation_once(self) -> None:
        if self.bridge is None or self.bot_client is None:
            return
        bot = await self.bot_client.get_me()
        external_bot_id = bot.get("id")
        if external_bot_id is None:
            raise RuntimeError("Telegram getMe response has no bot id")
        installation = await self.bridge.resolve_bot_installation(str(external_bot_id))
        self.installation_id = installation["installation_id"]
        self.team_id = installation["team_id"]

    async def poll_updates_once(self, *, timeout: int = 30) -> int:
        if self.bot_client is None or self.settings.transport_mode != "polling":
            return 0

        updates = await self.bot_client.get_updates(
            offset=self.next_update_offset,
            timeout=timeout,
        )
        accepted = 0
        for update in updates:
            update_id = update.get("update_id")
            if not isinstance(update_id, int):
                continue
            stored = self.spool.store_update(update_id, update, self._now())
            self.record_webhook("accepted" if stored else "duplicate")
            if stored:
                accepted += 1
            self.next_update_offset = update_id + 1
        self.record_depth()
        return accepted

    async def heartbeat_once(self) -> None:
        if self.bridge is None:
            return
        try:
            await self.bridge.heartbeat(
                gateway_id=self.settings.gateway_id,
                version=self.settings.version,
                queue_depth=self.spool.depth(),
                metadata={"spool_path": str(self.settings.spool_path)},
            )
        except Exception:
            HEARTBEAT_TOTAL.labels(status="error").inc()
            raise
        HEARTBEAT_TOTAL.labels(status="ok").inc()

    async def drain_once(self, *, limit: int | None = None) -> int:
        if self.bridge is None:
            return 0
        if self.installation_id is None or self.team_id is None:
            await self.resolve_installation_once()
        if self.installation_id is None or self.team_id is None:
            return 0
        batch = self.spool.claim_due(
            limit=limit or 20,
            lease_seconds=self.settings.lease_seconds,
        )
        if not batch:
            self.record_depth()
            return 0

        processed = 0
        for item in batch:
            with FORWARD_LATENCY.time():
                try:
                    await self.bridge.ingest_update(
                        item,
                        team_id=self.team_id,
                        installation_id=self.installation_id,
                        gateway_id=self.settings.gateway_id,
                        version=self.settings.version,
                    )
                except BridgeRequestError as exc:
                    if bridge_error_is_permanent(exc):
                        self.spool.mark_retry(
                            item.id,
                            attempts=item.attempts,
                            error=exc.detail,
                            max_attempts=1,
                        )
                        FORWARD_TOTAL.labels(status="dead_letter").inc()
                    else:
                        status = self.spool.mark_retry(
                            item.id,
                            attempts=item.attempts,
                            error=exc.detail,
                            retry_after_seconds=exc.retry_after_seconds,
                            max_attempts=self.settings.max_attempts,
                        )
                        FORWARD_TOTAL.labels(status=status).inc()
                except Exception as exc:
                    status = self.spool.mark_retry(
                        item.id,
                        attempts=item.attempts,
                        error=str(exc),
                        max_attempts=self.settings.max_attempts,
                    )
                    FORWARD_TOTAL.labels(status=status).inc()
                else:
                    self.spool.mark_sent(item.id)
                    FORWARD_TOTAL.labels(status="sent").inc()
                    processed += 1

        self.record_depth()
        return processed

    async def deliver_once(self, *, limit: int | None = None) -> int:
        """Lease outbox items, send via Bot API, acknowledge."""
        if self.bridge is None or self.bot_client is None:
            return 0

        # 1. Lease batch from main bridge
        leased = await self.bridge.lease_outbox(
            worker_id=self.settings.gateway_id,
            limit=limit or 20,
            lease_seconds=self.settings.lease_seconds,
        )
        if not leased.items:
            return 0

        # 2. Process each item
        delivered = 0
        for item in leased.items:
            delivery_error = None
            with DELIVER_LATENCY.time():
                try:
                    result = await self._deliver_item(item)
                    status = "sent"
                    provider_id = result.message_id if result else None
                except BotAPIError as exc:
                    if exc.permanent:
                        status = "dead_letter"
                    else:
                        status = "retry"
                        delivery_error = exc
                    provider_id = None
                except Exception as exc:
                    status = "retry"
                    delivery_error = exc
                    provider_id = None

            # 3. Acknowledge to main bridge
            await self.bridge.ack_outbox(
                delivery_id=item.delivery_id,
                status=status,
                provider_message_id=provider_id,
                last_error=str(delivery_error) if delivery_error else None,
                retry_after_seconds=(
                    getattr(delivery_error, "retry_after", None)
                    if delivery_error
                    else None
                ),
            )
            if status == "sent":
                delivered += 1
            DELIVER_TOTAL.labels(status=status).inc()

        return delivered

    async def _deliver_item(self, item: LeaseItem):
        """Send single outbox item via Bot API."""
        if self.bot_client is None:
            return None

        method = item.payload.get("method")
        if method == "sendMessage":
            return await self.bot_client.send_message(
                chat_id=item.target_chat_id or item.target_user_id,
                text=item.payload.get("text", ""),
                reply_to_message_id=item.payload.get("reply_to_message_id"),
                message_thread_id=item.payload.get("message_thread_id"),
                reply_markup=item.payload.get("reply_markup"),
            )
        elif method == "answerCallbackQuery":
            return await self.bot_client.answer_callback_query(
                callback_query_id=item.payload.get("callback_query_id", ""),
                text=item.payload.get("text"),
                show_alert=item.payload.get("show_alert", False),
            )
        elif method == "editMessageReplyMarkup":
            return await self.bot_client.edit_message_reply_markup(
                chat_id=item.target_chat_id,
                message_id=item.payload.get("message_id", ""),
                reply_markup=item.payload.get("reply_markup", {"inline_keyboard": []}),
            )
        return None

    async def run(self, stop_event: asyncio.Event) -> None:
        await self.sync_transport_mode()
        while not stop_event.is_set():
            try:
                await self.poll_updates_once(
                    timeout=max(1, int(self.settings.heartbeat_interval_seconds))
                )
                await self.drain_once()
                await self.deliver_once()
                await self.heartbeat_once()
            except Exception:
                pass
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.settings.worker_poll_interval,
                )
            except TimeoutError:
                continue

    async def close(self) -> None:
        if self.bridge is not None:
            await self.bridge.aclose()
        if self.bot_client is not None:
            await self.bot_client.close()


def build_runtime(settings: GatewaySettings | None = None) -> GatewayRuntime:
    settings = settings or GatewaySettings.from_env()
    spool = GatewaySpool(settings.spool_path)
    bridge = MainBridgeClient(
        base_url=settings.main_bridge_url,
        key_id=settings.bridge_key_id,
        key_secret=settings.bridge_key_secret,
        timeout_seconds=settings.bridge_timeout_seconds,
    )
    bot_client = TelegramBotClient(bot_token=settings.bot_token)
    return GatewayRuntime(settings=settings, spool=spool, bridge=bridge, bot_client=bot_client)
