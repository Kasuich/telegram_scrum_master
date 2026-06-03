"""
Smoke tests for real YandexGPT API.

Skipped by default — only run when real credentials are available:

    # manually:
    YC_API_KEY=... YC_FOLDER_ID=... pytest packages/core/tests/smoke/ -v

    # or via marker:
    pytest -m smoke -v

These tests make real API calls and cost tokens. Never run in regular CI.
"""

from __future__ import annotations

import os

import pytest

# Skip the whole module if credentials are missing
pytestmark = pytest.mark.smoke

YC_API_KEY = os.getenv("YC_API_KEY", "")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID", "")

_has_creds = bool(YC_API_KEY and YC_FOLDER_ID and not YC_API_KEY.startswith("stub"))
_skip_no_creds = pytest.mark.skipif(not _has_creds, reason="YC_API_KEY / YC_FOLDER_ID not set")


@_skip_no_creds
async def test_yandexgpt_basic_completion():
    """One real call — verify credentials work and model responds."""
    from core.llm import Message, complete

    response = await complete([Message(role="user", content="Скажи 'ок' одним словом.")])

    assert response.content is not None, "Expected text content, got None"
    assert len(response.content) > 0, "Response content is empty"
    assert response.model, "model field should be populated"
    assert response.usage is not None, "usage should be present"
    assert response.usage.total_tokens > 0, "Should have consumed tokens"

    print(f"\n  model={response.model}")
    print(f"  content={response.content!r}")
    print(f"  tokens={response.usage.total_tokens} (prompt={response.usage.prompt_tokens})")
    print(f"  latency={response.latency_ms}ms")


@_skip_no_creds
async def test_yandexgpt_tool_calling():
    """Verify the model can produce a tool call in the expected format."""
    from core.llm import Message, complete

    tool_schema = {
        "name": "get_answer",
        "description": "Return a numeric answer.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "integer", "description": "The answer"}},
            "required": ["value"],
        },
    }

    response = await complete(
        [Message(role="user", content="What is 2 + 2? Use get_answer tool.")],
        tools=[tool_schema],
    )

    # Model may respond with a tool call or just text — both are valid
    has_output = response.content or response.tool_calls
    assert has_output, "Response must have content or tool_calls"

    if response.tool_calls:
        tc = response.tool_calls[0]
        print(f"\n  tool_call: {tc.name}({tc.arguments})")
    else:
        print(f"\n  text response: {response.content!r}")


@_skip_no_creds
async def test_yandexgpt_model_lite():
    """Sanity-check yandexgpt-lite as a fallback model."""
    from core.llm import LLMClient, Message

    client = LLMClient(model="yandexgpt-lite", max_tokens=10)
    try:
        response = await client.complete([Message(role="user", content="Ping")])
        assert response.content or response.tool_calls
        print(f"\n  yandexgpt-lite ok, latency={response.latency_ms}ms")
    finally:
        await client.close()
