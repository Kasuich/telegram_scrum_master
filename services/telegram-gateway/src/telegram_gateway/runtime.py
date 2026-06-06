from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from prometheus_client import Counter, Gauge, Histogram

from telegram_gateway.bridge import (
    BridgeRequestError,
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


@dataclass(slots=True)
class GatewayRuntime:
    settings: GatewaySettings
    spool: GatewaySpool
    bridge: MainBridgeClient | None
    auto_start_workers: bool = True

    def _now(self) -> datetime:
        return datetime.now(tz=timezone.utc)

    def record_webhook(self, status: str) -> None:
        WEBHOOK_TOTAL.labels(status=status).inc()
        SPOOL_DEPTH.set(self.spool.depth())

    def record_depth(self) -> None:
        SPOOL_DEPTH.set(self.spool.depth())

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

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self.drain_once()
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


def build_runtime(settings: GatewaySettings | None = None) -> GatewayRuntime:
    settings = settings or GatewaySettings.from_env()
    spool = GatewaySpool(settings.spool_path)
    bridge = MainBridgeClient(
        base_url=settings.main_bridge_url,
        key_id=settings.bridge_key_id,
        key_secret=settings.bridge_key_secret,
        timeout_seconds=settings.bridge_timeout_seconds,
    )
    return GatewayRuntime(settings=settings, spool=spool, bridge=bridge)
