"""
Example 03: LLM integration with YandexGPT.

Shows how to use LLMClient for completions, tool calling, and streaming.
Run: python -m examples.03_llm_integration  (from packages/core/)
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/pm_agent")
os.environ.setdefault("YC_API_KEY", "your_real_api_key_here")
os.environ.setdefault("YC_FOLDER_ID", "your_folder_id_here")
os.environ.setdefault("TRACKER_TOKEN", "example_oauth_token_12345678901234567890")
os.environ.setdefault("TRACKER_ORG_ID", "12345678901234567890")

MOCK_TEXT = {
    "result": {
        "message": {"text": "Задача TEST-42 создана успешно.", "role": "assistant"},
        "usage": {"inputTokensCount": 30, "outputTokensCount": 12, "totalTokensCount": 42},
        "status": "COMPLETED",
    }
}

MOCK_TOOL_CALL = {
    "result": {
        "message": {
            "functionCall": {
                "name": "tracker_create_issue",
                "args": {"queue": "TEST", "summary": "Оптимизировать запросы к БД"},
            }
        },
        "usage": {"inputTokensCount": 45, "outputTokensCount": 18, "totalTokensCount": 63},
        "status": "COMPLETED",
    }
}


def make_mock_http(response_body: dict) -> AsyncMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = response_body
    mock_resp.raise_for_status = MagicMock()
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    return mock_http


async def demo_text_completion() -> None:
    from core.llm import LLMClient, Message

    print("=== Text completion ===")
    with patch("core.llm.LLMClient.client") as prop:
        prop.__get__ = MagicMock(return_value=make_mock_http(MOCK_TEXT))

        client = LLMClient(temperature=0.5)
        response = await client.complete([
            Message(role="system", content="Ты PM-агент. Отвечай кратко."),
            Message(role="user", content="Создай задачу на оптимизацию БД"),
        ])
        await client.close()

    print(f"Content: {response.content}")
    print(f"Model: {response.model}")
    print(f"Tokens: {response.usage.total_tokens}")
    print(f"Latency: {response.latency_ms}ms")


async def demo_tool_calling() -> None:
    from core.llm import LLMClient, Message

    print("\n=== Tool calling ===")
    tools = [
        {
            "name": "tracker_create_issue",
            "description": "Create a Yandex Tracker issue",
            "parameters": {
                "type": "object",
                "properties": {
                    "queue": {"type": "string", "description": "Queue key"},
                    "summary": {"type": "string", "description": "Issue title"},
                },
                "required": ["queue", "summary"],
            },
        }
    ]

    with patch("core.llm.LLMClient.client") as prop:
        prop.__get__ = MagicMock(return_value=make_mock_http(MOCK_TOOL_CALL))

        client = LLMClient()
        response = await client.complete(
            [Message(role="user", content="Создай задачу на оптимизацию БД")],
            tools=tools,
        )
        await client.close()

    if response.tool_calls:
        tc = response.tool_calls[0]
        print(f"Tool: {tc.name}")
        print(f"Args: {tc.arguments}")
    else:
        print(f"Text response: {response.content}")


async def demo_streaming() -> None:
    from core.llm import LLMClient, Message

    print("\n=== Streaming ===")
    with patch("core.llm.LLMClient.client") as prop:
        prop.__get__ = MagicMock(return_value=make_mock_http(MOCK_TEXT))

        client = LLMClient()
        chunks = []
        async for chunk in client.stream_complete([
            Message(role="user", content="Привет!")
        ]):
            chunks.append(chunk)
        await client.close()

    print(f"Chunks received: {len(chunks)}")
    print(f"Assembled: {''.join(chunks)}")


def main() -> None:
    asyncio.run(demo_text_completion())
    asyncio.run(demo_tool_calling())
    asyncio.run(demo_streaming())


if __name__ == "__main__":
    main()
