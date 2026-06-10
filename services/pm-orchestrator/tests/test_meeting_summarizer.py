"""Tests for MeetingSummarizerAgent."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from core.react import AgentResult
from core.tools import ToolRegistry
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("YC_API_KEY", "stub_key_00000000000000000000")
os.environ.setdefault("YC_FOLDER_ID", "b1g0000000000000000")
os.environ.setdefault("TRACKER_TOKEN", "stub_token_000000000000000000000")
os.environ.setdefault("TRACKER_ORG_ID", "000000000000")
os.environ.setdefault("TRACKER_ORG_TYPE", "cloud")
os.environ.setdefault("OPENROUTER_API_KEY", "sk-or-v1-test-stub-key-0000000000")

SAMPLE_SUMMARY = """## Краткое резюме
Обсудили релиз MVP и блокеры по VPN.

## Ключевые решения
- Релиз переносим на следующую неделю.

## Action items
| # | Задача | Владелец | Дедлайн | Приоритет |
|---|--------|----------|---------|-----------|
| 1 | Купить VPN | Сергей | — | высокий |

## Риски и блокеры
- Нет VPN для ТГ-бота.

## Открытые вопросы
- Кто возьмёт интеграцию с Трекером?
"""


def _text_response(text: str) -> dict:
    return {
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 50, "total_tokens": 60},
    }


def _http_ok(data: dict) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture(autouse=True)
def _clean_registry():
    ToolRegistry().clear()
    yield
    ToolRegistry().clear()


class TestMeetingSummarizerAgent:
    def test_agent_config(self):
        from pm_orchestrator.agents.meeting_summarizer import MeetingSummarizerAgent

        agent = MeetingSummarizerAgent()
        assert agent.name == "meeting_summarizer"
        assert agent.tools == []
        assert agent.action_only is False
        assert "Краткое резюме" in agent.prompt

    @pytest.mark.asyncio
    async def test_invoke_returns_markdown_summary(self):
        from pm_orchestrator.agents.meeting_summarizer import MeetingSummarizerAgent
        from pm_orchestrator.orchestrator import OrchestratorService

        svc = OrchestratorService()
        svc._register(MeetingSummarizerAgent())

        transcript = "Сергей: нужен VPN для бота. Релиз переносим."
        mock_post = AsyncMock(return_value=_http_ok(_text_response(SAMPLE_SUMMARY)))

        with patch("httpx.AsyncClient.post", mock_post):
            result = await svc.invoke("meeting_summarizer", transcript, "sum-1")

        assert result.reply is not None
        assert "## Краткое резюме" in result.reply
        assert "## Action items" in result.reply
        assert result.pending_confirm is None


class TestMeetingSummarizerDiscovery:
    def test_discover_agents_includes_meeting_summarizer(self):
        from pm_orchestrator.orchestrator import OrchestratorService

        svc = OrchestratorService()
        svc.discover_agents()
        names = [a["name"] for a in svc.list_agents()]
        assert "meeting_summarizer" in names
        assert "pm_agent" in names

    def test_rpc_health_lists_meeting_summarizer(self):
        from pm_orchestrator import rpc

        rpc._svc._runners.clear()
        rpc._svc.discover_agents()

        client = TestClient(rpc.app)
        r = client.get("/health")
        assert r.status_code == 200
        assert "meeting_summarizer" in r.json()["agents"]

    def test_rpc_invoke_meeting_summarizer(self):
        from pm_orchestrator.agents.meeting_summarizer import MeetingSummarizerAgent
        from pm_orchestrator import rpc

        rpc._svc._runners.clear()
        rpc._svc._register(MeetingSummarizerAgent())

        with patch.object(
            rpc._svc._runners["meeting_summarizer"],
            "invoke",
            AsyncMock(return_value=AgentResult(reply=SAMPLE_SUMMARY, session_id="s1", steps=[])),
        ):
            client = TestClient(rpc.app)
            r = client.post(
                "/rpc",
                json={
                    "jsonrpc": "2.0",
                    "method": "invoke",
                    "params": {
                        "agent": "meeting_summarizer",
                        "message": "транскрипт встречи",
                        "session_id": "s1",
                    },
                    "id": 1,
                },
            )

        assert r.status_code == 200
        assert "## Краткое резюме" in r.json()["result"]["reply"]
