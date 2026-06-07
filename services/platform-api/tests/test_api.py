"""
Tests for platform-api HTTP endpoints.
All orchestrator calls are mocked via rpc_client patch.
"""

from __future__ import annotations

import hmac
import os
import time
import uuid
from hashlib import sha256
from unittest.mock import AsyncMock, patch

import pytest
from core.invocation import InvocationContext
from core.react import AgentResult, PendingConfirm
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("YC_API_KEY", "stub_key_00000000000000000000")
os.environ.setdefault("YC_FOLDER_ID", "b1g0000000000000000")
os.environ.setdefault("TRACKER_TOKEN", "stub_token_000000000000000000000")
os.environ.setdefault("TRACKER_ORG_ID", "000000000000")
os.environ.setdefault("TRACKER_ORG_TYPE", "cloud")
os.environ.setdefault("TELEGRAM_BRIDGE_HMAC_KEYS", "test-key:supersecret")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    from platform_api.main import app
    from platform_api.telegram_bridge import _db_session

    async def _fake_session():
        yield object()

    with (
        patch("platform_api.rpc_client.list_agents", AsyncMock(return_value=[])),
    ):
        app.dependency_overrides[_db_session] = _fake_session
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.clear()


def _mock_rpc(invoke=None, resume=None, list_agents=None, get_actions=None):
    return patch.multiple(
        "platform_api.rpc_client",
        invoke=AsyncMock(return_value=invoke),
        resume=AsyncMock(return_value=resume),
        list_agents=AsyncMock(return_value=list_agents or []),
        get_actions=AsyncMock(return_value=get_actions or []),
    )


def _bridge_headers(path: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())
    payload = "\n".join(
        [
            "POST",
            path,
            timestamp,
            nonce,
            sha256(body).hexdigest(),
        ]
    ).encode("utf-8")
    signature = hmac.new(b"supersecret", payload, sha256).hexdigest()
    return {
        "X-Telegram-Bridge-Key-Id": "test-key",
        "X-Telegram-Bridge-Timestamp": timestamp,
        "X-Telegram-Bridge-Nonce": nonce,
        "X-Telegram-Bridge-Signature": signature,
    }


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def _text(reply: str = "Done", sid: str = "s1") -> AgentResult:
    return AgentResult(reply=reply, session_id=sid, steps=[{"kind": "final", "content": reply}])


def _confirm(confirm_id: str = "c1", sid: str = "s1") -> AgentResult:
    return AgentResult(
        pending_confirm=PendingConfirm(
            confirm_id=confirm_id,
            tool_name="tracker_create_issue",
            tool_args={"queue": "TEST", "summary": "Bug"},
            risk="medium",
            prompt="Create?",
        ),
        session_id=sid,
        steps=[{"kind": "confirm_wait", "confirm_id": confirm_id}],
    )


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_ok(self, client):
        assert client.get("/health").json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /agents
# ---------------------------------------------------------------------------


class TestAgents:
    def test_returns_list(self, client):
        agents = [{"name": "pm_agent", "description": "PM agent"}]
        with _mock_rpc(list_agents=agents):
            r = client.get("/agents")
        assert r.status_code == 200
        assert r.json() == agents


# ---------------------------------------------------------------------------
# POST /chat  (default agent shortcut)
# ---------------------------------------------------------------------------


class TestChat:
    def test_text_reply(self, client):
        with _mock_rpc(invoke=_text("Всё готово!")):
            r = client.post("/chat", json={"message": "Привет", "session_id": "s1"})
        assert r.status_code == 200
        data = r.json()
        assert data["reply"] == "Всё готово!"
        assert data["pending_confirm"] is None

    def test_pending_confirm(self, client):
        with _mock_rpc(invoke=_confirm()):
            r = client.post("/chat", json={"message": "Создай задачу", "session_id": "s1"})
        assert r.status_code == 200
        pc = r.json()["pending_confirm"]
        assert pc["confirm_id"] == "c1"
        assert pc["risk"] == "medium"

    def test_empty_message_422(self, client):
        r = client.post("/chat", json={"message": "", "session_id": "s1"})
        assert r.status_code == 422

    def test_long_summary_message_accepted(self, client):
        long_text = "Резюме лекции: " + ("текст саммари. " * 400)
        with _mock_rpc(invoke=_text("Доска создана")):
            r = client.post("/chat", json={"message": long_text, "session_id": "s1"})
        assert r.status_code == 200
        assert len(long_text) > 4096

    def test_rpc_error_returns_500(self, client):
        exc = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("platform_api.rpc_client.invoke", exc):
            r = client.post("/chat", json={"message": "Hi", "session_id": "s1"})
        assert r.status_code == 500

    def test_uses_default_agent(self, client):
        with patch("platform_api.rpc_client.invoke", AsyncMock(return_value=_text())) as m:
            client.post("/chat", json={"message": "Hi", "session_id": "s1"})
        assert m.call_args[0][0] == "pm_agent"

    def test_forwards_context(self, client):
        with patch("platform_api.rpc_client.invoke", AsyncMock(return_value=_text())) as m:
            client.post(
                "/chat",
                json={
                    "message": "Hi",
                    "session_id": "telegram:s1",
                    "context": {
                        "channel": "telegram",
                        "chat_id": "-1001",
                        "message_id": "42",
                    },
                },
            )
        assert m.call_args.kwargs["context"] == InvocationContext(
            channel="telegram",
            chat_id="-1001",
            message_id="42",
        )


# ---------------------------------------------------------------------------
# POST /agents/{name}/chat  (per-agent route)
# ---------------------------------------------------------------------------


class TestAgentChat:
    def test_routes_to_correct_agent(self, client):
        with patch("platform_api.rpc_client.invoke", AsyncMock(return_value=_text())) as m:
            client.post("/agents/my_bot/chat", json={"message": "Hi", "session_id": "s1"})
        assert m.call_args[0][0] == "my_bot"


# ---------------------------------------------------------------------------
# POST /confirm/{id}
# ---------------------------------------------------------------------------


class TestConfirm:
    def test_approved(self, client):
        with patch("platform_api.rpc_client.resume", AsyncMock(return_value=_text("Done!"))) as m:
            r = client.post("/confirm/c1", json={"approved": True})
        assert r.status_code == 200
        assert r.json()["reply"] == "Done!"
        m.assert_called_once_with("c1", True)

    def test_rejected(self, client):
        with patch("platform_api.rpc_client.resume", AsyncMock(return_value=_text("OK"))):
            r = client.post("/confirm/c1", json={"approved": False})
        assert r.status_code == 200

    def test_not_found_returns_404(self, client):
        with patch("platform_api.rpc_client.resume", AsyncMock()) as m:
            m.side_effect = RuntimeError("Confirm not found: 'x'")
            r = client.post("/confirm/x", json={"approved": True})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /actions
# ---------------------------------------------------------------------------


class TestActions:
    def test_returns_list(self, client):
        actions = [{"kind": "tool_call", "session_id": "s1"}]
        with _mock_rpc(get_actions=actions):
            r = client.get("/actions")
        assert r.status_code == 200
        assert r.json() == actions

    def test_passes_session_filter(self, client):
        with patch("platform_api.rpc_client.get_actions", AsyncMock(return_value=[])) as m:
            client.get("/actions?session_id=s1&limit=10")
        m.assert_called_once_with(session_id="s1", limit=10)


# ---------------------------------------------------------------------------
# GET /metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_prometheus_format(self, client):
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]


class TestTelegramBridge:
    def test_heartbeat_requires_auth(self, client):
        r = client.post(
            "/internal/telegram/v1/heartbeat",
            json={"gateway_id": "gw-1", "version": "1.0.0"},
        )
        assert r.status_code == 401

    def test_heartbeat_ok(self, client):
        body = b'{"gateway_id":"gw-1","version":"1.0.0","queue_depth":3}'
        r = client.post(
            "/internal/telegram/v1/heartbeat",
            content=body,
            headers={
                **_bridge_headers("/internal/telegram/v1/heartbeat", body),
                "content-type": "application/json",
            },
        )
        assert r.status_code == 200
        assert r.json()["gateway_id"] == "gw-1"

    def test_ingest_delegates_to_helper(self, client):
        body = (
            b'{"team_id":"00000000-0000-0000-0000-000000000001",'
            b'"installation_id":"00000000-0000-0000-0000-000000000002",'
            b'"update_id":1,"payload":{"message":{"message_id":42}}}'
        )
        with patch(
            "platform_api.telegram_bridge.ingest_event",
            AsyncMock(return_value={"update_id": "u1", "duplicate": False}),
        ) as mocked:
            r = client.post(
                "/internal/telegram/v1/events:ingest",
                content=body,
                headers={
                    **_bridge_headers("/internal/telegram/v1/events:ingest", body),
                    "content-type": "application/json",
                },
            )
        assert r.status_code == 200
        assert r.json()["update_id"] == "u1"
        mocked.assert_awaited()

    def test_lease_delegates_to_helper(self, client):
        body = b'{"worker_id":"gw-1","limit":2,"lease_seconds":60}'
        with patch(
            "platform_api.telegram_bridge.lease_outbox",
            AsyncMock(return_value=[]),
        ) as mocked:
            r = client.post(
                "/internal/telegram/v1/outbox:lease",
                content=body,
                headers={
                    **_bridge_headers("/internal/telegram/v1/outbox:lease", body),
                    "content-type": "application/json",
                },
            )
        assert r.status_code == 200
        assert r.json() == {"items": []}
        mocked.assert_awaited()

    def test_ack_delegates_to_helper(self, client):
        body = b'{"status":"sent","provider_message_id":"99"}'
        with patch(
            "platform_api.telegram_bridge.ack_outbox",
            AsyncMock(return_value={"delivery_id": "d1", "status": "sent"}),
        ) as mocked:
            r = client.post(
                "/internal/telegram/v1/outbox/00000000-0000-0000-0000-000000000010:ack",
                content=body,
                headers={
                    **_bridge_headers(
                        "/internal/telegram/v1/outbox/00000000-0000-0000-0000-000000000010:ack",
                        body,
                    ),
                    "content-type": "application/json",
                },
            )
        assert r.status_code == 200
        assert r.json()["status"] == "sent"
        mocked.assert_awaited()
