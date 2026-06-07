from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

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
