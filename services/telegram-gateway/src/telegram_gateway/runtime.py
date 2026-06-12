from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from prometheus_client import Counter, Gauge, Histogram

from telegram_gateway.bot_api import BotAPIError, SendResult, TelegramBotClient
from telegram_gateway.bridge import (
    BridgeRequestError,
    LeaseItem,
    MainBridgeClient,
    bridge_error_is_permanent,
)
from telegram_gateway.formatting import render_telegram_html
from telegram_gateway.settings import GatewaySettings
from telegram_gateway.spool import GatewaySpool
from telegram_gateway.streaming import plan_pacing, stream_output, thinking_html

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
    rng: random.Random = field(default_factory=random.Random)
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    # chat_id → (status message_id, background animation task) for the early
    # "thinking" status, so the reply delivery can cancel + delete it.
    status_messages: dict[str, tuple[str, "asyncio.Task[None]"]] = field(default_factory=dict)
    draft_seq: int = 0

    def _next_draft_id(self) -> int:
        """Unique int32 draft id per stream (stable across updates of one
        stream, distinct between streams)."""
        self.draft_seq = (self.draft_seq + 1) % 2_147_483_647
        return self.draft_seq + 1

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
        elif self.settings.transport_mode == "webhook" and self.settings.webhook_url:
            # Register (or refresh) the webhook with Telegram on startup so it
            # points at the current public URL. Idempotent: Telegram accepts the
            # same URL repeatedly. Skipped when no public base URL is configured.
            await self.bot_client.set_webhook(
                url=self.settings.webhook_url,
                secret_token=self.settings.webhook_secret,
            )

    async def register_commands(self) -> None:
        """Publish the bot's slash-command menu on startup (best-effort).

        Commands work by text parsing regardless of this; setMyCommands only
        populates Telegram's "/" autocomplete. Registered globally, so /audit
        is visible to everyone — non-leads get a polite refusal when they run
        it (access is enforced server-side, not by hiding the command).
        """
        if self.bot_client is None:
            return
        commands = [
            {"command": "audit", "description": "Аудит доски (для тимлидов)"},
        ]
        try:
            await self.bot_client.set_my_commands(commands)
        except BotAPIError:
            pass

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
                    getattr(delivery_error, "retry_after", None) if delivery_error else None
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
            raw_text = str(item.payload.get("text", ""))
            metadata = item.payload.get("metadata") or {}
            if metadata.get("status"):
                return await self._deliver_status(item)
            text = render_telegram_html(raw_text)
            # Any real reply replaces the early "thinking" status (groups too).
            chat_id = item.target_chat_id or item.target_user_id
            await self._clear_status(str(chat_id))
            # Private chats get native draft streaming; groups get a plain send.
            if self._should_stream(item, raw_text, text):
                return await self._deliver_streaming_reply(item, raw_text, text)
            return await self.bot_client.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=item.payload.get("reply_to_message_id"),
                message_thread_id=item.payload.get("message_thread_id"),
                reply_markup=item.payload.get("reply_markup"),
                parse_mode="HTML",
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

    def _should_stream(self, item: LeaseItem, raw_text: str, rendered: str) -> bool:
        """Native draft streaming applies only to replies platform-api flagged
        as private-chat (``sendMessageDraft`` errors in groups) and worth
        animating. Groups fall through to a plain send."""
        if not self.settings.stream_enabled or self.bot_client is None:
            return False
        metadata = item.payload.get("metadata") or {}
        if not metadata.get("stream"):
            return False
        # No room for a draft preview on plain confirmations / buttons.
        if item.payload.get("reply_markup"):
            return False
        if len(raw_text) < self.settings.stream_min_chars:
            return False
        # The committed message can't exceed the 4096-char limit.
        return len(rendered) <= TelegramBotClient.MAX_MESSAGE_LENGTH

    async def _deliver_status(self, item: LeaseItem) -> SendResult | None:
        """Deliver the early "thinking" status and animate it in the background.

        This item is enqueued by platform-api *before* the agent runs, so it
        reaches the user while the agent is still working. The first beat is sent
        now; subsequent stage beats are edited in by a background task so the
        delivery loop isn't blocked. The reply delivery later cancels that task
        and removes the message.
        """
        assert self.bot_client is not None
        chat_id = item.target_chat_id or item.target_user_id
        reply_to = item.payload.get("reply_to_message_id")
        thread_id = item.payload.get("message_thread_id")

        # A late status is worse than none: if the reply already cleared this
        # chat (fast agent), don't post an orphan.
        try:
            status = await self.bot_client.send_message(
                chat_id=chat_id,
                text=thinking_html(0, self.rng),
                reply_to_message_id=reply_to,
                message_thread_id=thread_id,
                parse_mode="HTML",
            )
        except BotAPIError:
            return None

        key = str(chat_id)
        await self._clear_status(key)  # never keep two statuses for one chat
        task = asyncio.create_task(self._animate_status(key, status.message_id))
        self.status_messages[key] = (status.message_id, task)
        return status

    async def _animate_status(self, chat_id: str, message_id: str) -> None:
        """Edit the status message through stage beats until cancelled."""
        if self.bot_client is None:
            return
        for index in range(1, self.settings.stream_status_max_frames):
            await self.sleep(self.settings.stream_status_interval)
            try:
                await self.bot_client.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=thinking_html(index, self.rng),
                    parse_mode="HTML",
                )
            except BotAPIError:
                # e.g. "message is not modified" or rate limit — keep cycling.
                continue

    async def _clear_status(self, chat_id: str) -> None:
        """Cancel the animation and delete the thinking status for a chat."""
        entry = self.status_messages.pop(chat_id, None)
        if entry is None:
            return
        message_id, task = entry
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        await self._try_delete(chat_id, message_id)

    async def _deliver_streaming_reply(
        self,
        item: LeaseItem,
        raw_text: str,
        rendered: str,
    ) -> SendResult | None:
        """Native draft streaming for a private chat, then commit the answer.

        The answer is streamed into a live draft bubble via ``sendMessageDraft``
        (Telegram draws the animated dots natively); the draft is ephemeral, so a
        real ``sendMessage`` with the HTML formatting follows to persist it. The
        caller has already cleared the thinking status. Any Bot API hiccup — e.g.
        the chat isn't actually private — degrades to a plain final send.
        """
        assert self.bot_client is not None
        chat_id = item.target_chat_id or item.target_user_id
        reply_to = item.payload.get("reply_to_message_id")
        thread_id = item.payload.get("message_thread_id")

        async def _plain_send() -> SendResult:
            return await self.bot_client.send_message(
                chat_id=chat_id,
                text=rendered,
                reply_to_message_id=reply_to,
                message_thread_id=thread_id,
                parse_mode="HTML",
            )

        chunk_size, delay = plan_pacing(
            len(raw_text),
            cps=self.settings.stream_cps,
            interval=self.settings.stream_interval,
            max_steps=self.settings.stream_max_steps,
            min_duration=self.settings.stream_min_duration,
            max_duration=self.settings.stream_max_duration,
        )
        draft_id = self._next_draft_id()
        accumulated = ""
        try:
            async for chunk in stream_output(
                raw_text, chunk_size=chunk_size, delay=delay, sleep=self.sleep
            ):
                accumulated += chunk
                # Plain text draft — Telegram appends the animated dots itself.
                await self.bot_client.send_message_draft(
                    chat_id=chat_id, draft_id=draft_id, text=accumulated
                )
            # Clear the plain draft so the final send commits as a clean,
            # formatted message — a draft left active otherwise finalizes the
            # reply as its plain text, stripping links/formatting.
            await self.bot_client.send_message_draft(
                chat_id=chat_id, draft_id=draft_id, text=""
            )
        except BotAPIError:
            # Draft stream failed (e.g. group chat) — fall through and commit.
            pass

        # The draft is ephemeral; a real send is required to persist the reply.
        return await _plain_send()

    async def _try_delete(self, chat_id: str | None, message_id: str) -> None:
        if self.bot_client is None:
            return
        try:
            await self.bot_client.delete_message(chat_id=chat_id, message_id=message_id)
        except BotAPIError:
            pass

    async def run(self, stop_event: asyncio.Event) -> None:
        await self.sync_transport_mode()
        await self.register_commands()
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
