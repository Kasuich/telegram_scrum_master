from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from telegram_gateway.bridge import BridgeRequestError
from telegram_gateway.main import create_app
from telegram_gateway.runtime import GatewayRuntime
from telegram_gateway.settings import GatewaySettings
from telegram_gateway.spool import GatewaySpool


class FakeBridge:
    def __init__(self) -> None:
        self.ingest_update = AsyncMock(return_value={})
        self.heartbeat = AsyncMock(return_value={})
        self.resolve_bot_installation = AsyncMock(
            return_value={"installation_id": "inst-1", "team_id": "team-1"}
        )
        self.aclose = AsyncMock(return_value=None)


def make_runtime(tmp_path: Path) -> GatewayRuntime:
    settings = GatewaySettings(
        bot_token="bot-token",
        webhook_secret="hook-secret",
        main_bridge_url="https://main.example",
        bridge_key_id="key-1",
        bridge_key_secret="secret-1",
        spool_path=tmp_path / "spool.db",
    )
    bridge = FakeBridge()
    return GatewayRuntime(
        settings=settings,
        spool=GatewaySpool(settings.spool_path),
        bridge=bridge,
        auto_start_workers=False,
        installation_id="inst-1",
        team_id="team-1",
    )


@pytest.fixture()
def runtime(tmp_path: Path) -> GatewayRuntime:
    return make_runtime(tmp_path)


@pytest.fixture()
def client(runtime: GatewayRuntime) -> TestClient:
    app = create_app(runtime=runtime)
    with TestClient(app) as client:
        yield client


def test_webhook_rejects_invalid_secret(client: TestClient) -> None:
    response = client.post(
        "/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        json={"update_id": 1, "message": {"text": "hi"}},
    )
    assert response.status_code == 401


def test_webhook_stores_update_idempotently(client: TestClient, runtime: GatewayRuntime) -> None:
    payload = {"update_id": 42, "message": {"message_id": 7, "text": "hello"}}
    headers = {"X-Telegram-Bot-Api-Secret-Token": runtime.settings.webhook_secret}

    first = client.post("/webhook", headers=headers, json=payload)
    second = client.post("/webhook", headers=headers, json=payload)

    assert first.status_code == 200
    assert first.json() == {"accepted": True, "duplicate": False, "update_id": 42}
    assert second.json()["duplicate"] is True
    assert runtime.spool.depth() == 1


def test_health_ready_reports_queue_depth(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["queue_depth"] == 0


@pytest.mark.asyncio
async def test_drain_once_forwards_to_main(runtime: GatewayRuntime) -> None:
    runtime.spool.store_update(
        99,
        {"update_id": 99, "message": {"message_id": 1, "text": "ping"}},
        datetime.now(tz=timezone.utc),
    )
    runtime.bridge.ingest_update = AsyncMock(return_value={})  # type: ignore[method-assign]

    processed = await runtime.drain_once(limit=10)

    assert processed == 1
    runtime.bridge.ingest_update.assert_awaited_once()
    assert runtime.bridge.ingest_update.await_args.kwargs["installation_id"] == "inst-1"
    assert runtime.bridge.ingest_update.await_args.kwargs["team_id"] == "team-1"
    assert runtime.spool.depth() == 0


@pytest.mark.asyncio
async def test_drain_once_retries_transient_bridge_errors(runtime: GatewayRuntime) -> None:
    runtime.spool.store_update(
        100,
        {"update_id": 100, "message": {"message_id": 1, "text": "ping"}},
        datetime.now(tz=timezone.utc),
    )

    runtime.bridge.ingest_update = AsyncMock(  # type: ignore[method-assign]
        side_effect=BridgeRequestError(status_code=503, detail="upstream unavailable")
    )

    processed = await runtime.drain_once(limit=10)

    assert processed == 0
    assert runtime.spool.depth() == 1


def test_metrics_endpoint_exposes_gateway_counters(
    client: TestClient,
    runtime: GatewayRuntime,
) -> None:
    runtime.record_webhook("accepted")
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "telegram_gateway_webhook_total" in response.text
    assert "telegram_gateway_drain_loop_seconds" in response.text
    assert "telegram_gateway_deliver_loop_seconds" in response.text


@pytest.mark.asyncio
async def test_deliver_loop_runs_while_drain_slow(tmp_path: Path) -> None:
    """Deliver loop ticks while drain loop is blocked (parallel workers)."""
    runtime = make_runtime(tmp_path)
    stop = asyncio.Event()
    drain_started = asyncio.Event()
    deliver_calls: list[float] = []

    async def slow_drain_once(_self: GatewayRuntime, **_kwargs: object) -> int:
        drain_started.set()
        await asyncio.sleep(0.15)
        return 0

    async def track_deliver_once(_self: GatewayRuntime, **_kwargs: object) -> int:
        deliver_calls.append(time.monotonic())
        return 0

    with (
        patch.object(GatewayRuntime, "poll_updates_once", AsyncMock(return_value=0)),
        patch.object(GatewayRuntime, "drain_once", slow_drain_once),
        patch.object(GatewayRuntime, "deliver_once", track_deliver_once),
        patch.object(GatewayRuntime, "heartbeat_once", AsyncMock()),
    ):
        drain_task = asyncio.create_task(runtime._drain_loop(stop))
        deliver_task = asyncio.create_task(runtime._deliver_loop(stop))
        await asyncio.wait_for(drain_started.wait(), timeout=2.0)
        await asyncio.sleep(0.05)
        assert len(deliver_calls) >= 1
        stop.set()
        await asyncio.wait_for(asyncio.gather(drain_task, deliver_task), timeout=2.0)


@pytest.mark.asyncio
async def test_run_starts_drain_and_deliver_loops(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.settings = replace(runtime.settings, worker_poll_interval=0.05)
    drain_mock = AsyncMock(return_value=0)
    deliver_mock = AsyncMock(return_value=0)
    with (
        patch.object(GatewayRuntime, "poll_updates_once", AsyncMock(return_value=0)),
        patch.object(GatewayRuntime, "drain_once", drain_mock),
        patch.object(GatewayRuntime, "deliver_once", deliver_mock),
        patch.object(GatewayRuntime, "heartbeat_once", AsyncMock()),
        patch.object(GatewayRuntime, "sync_transport_mode", AsyncMock()),
        patch.object(GatewayRuntime, "register_commands", AsyncMock()),
    ):
        stop = asyncio.Event()
        run_task = asyncio.create_task(runtime.run(stop))
        await asyncio.sleep(0.08)
        stop.set()
        await asyncio.wait_for(run_task, timeout=2.0)

    assert drain_mock.await_count >= 1
    assert deliver_mock.await_count >= 1
