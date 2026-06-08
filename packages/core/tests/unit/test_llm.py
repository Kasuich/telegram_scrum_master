"""
Tests for core.llm — LLMClient, complete(), data models.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from core.exceptions import LLMError
from core.llm import (
    LLMClient,
    LLMResponse,
    Message,
    TokenUsage,
    ToolCall,
    complete,
)

# ---------------------------------------------------------------------------
# Environment stub — required by get_config() inside LLMClient.__init__
# ---------------------------------------------------------------------------

ENV = {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test_db",
    "YC_API_KEY": "test_api_key_12345678901234567890",
    "YC_FOLDER_ID": "b1g1234567890abcdef",
    "TRACKER_TOKEN": "test_tracker_token_12345678901234567890",
    "TRACKER_ORG_ID": "12345678901234567890",
}

# ---------------------------------------------------------------------------
# Mock API responses (OpenAI Responses API format)
# ---------------------------------------------------------------------------

MOCK_TEXT_RESPONSE = {
    "output": [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Hello, World!"}],
        }
    ],
    "output_text": "Hello, World!",
    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    "status": "completed",
}

MOCK_TOOL_CALL_RESPONSE = {
    "output": [
        {
            "type": "function_call",
            "call_id": "fc_1",
            "name": "tracker_create_issue",
            "arguments": '{"queue": "TEST", "summary": "Fix bug"}',
        }
    ],
    "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
    "status": "completed",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_httpx_response(data: dict[str, Any], status_code: int = 200) -> MagicMock:
    """Build a minimal httpx.Response mock."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data)
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ===========================================================================
# 1. TestLLMModels
# ===========================================================================


class TestLLMModels:
    """Tests for LLM data model classes."""

    def test_message_valid_roles(self) -> None:
        """Message accepts all valid roles."""
        for role in ("system", "user", "assistant"):
            msg = Message(role=role, content="hi")
            assert msg.role == role
            assert msg.content == "hi"

    def test_message_invalid_role(self) -> None:
        """Message rejects unknown roles."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Message(role="bot", content="hi")  # type: ignore[arg-type]

    def test_tool_call_defaults(self) -> None:
        """ToolCall has empty dict as default arguments."""
        tc = ToolCall(name="my_tool")
        assert tc.name == "my_tool"
        assert tc.arguments == {}

    def test_tool_call_with_arguments(self) -> None:
        """ToolCall stores arbitrary arguments dict."""
        tc = ToolCall(name="tracker_create_issue", arguments={"queue": "TEST", "summary": "s"})
        assert tc.arguments["queue"] == "TEST"

    def test_token_usage_defaults(self) -> None:
        """TokenUsage defaults to zero counts."""
        usage = TokenUsage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0

    def test_token_usage_explicit(self) -> None:
        """TokenUsage stores explicit values."""
        usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        assert usage.prompt_tokens == 10
        assert usage.completion_tokens == 5
        assert usage.total_tokens == 15

    def test_llm_response_text(self) -> None:
        """LLMResponse with text content."""
        resp = LLMResponse(content="hello", model="yandexgpt-pro", latency_ms=100)
        assert resp.content == "hello"
        assert resp.tool_calls is None
        assert resp.model == "yandexgpt-pro"
        assert resp.latency_ms == 100

    def test_llm_response_tool_calls(self) -> None:
        """LLMResponse with tool_calls list."""
        tc = ToolCall(name="t", arguments={"k": "v"})
        resp = LLMResponse(tool_calls=[tc], model="yandexgpt-pro")
        assert resp.content is None
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "t"

    def test_llm_response_defaults(self) -> None:
        """LLMResponse optional fields default to None / 0."""
        resp = LLMResponse(model="yandexgpt-pro")
        assert resp.content is None
        assert resp.tool_calls is None
        assert resp.usage is None
        assert resp.latency_ms == 0
        assert resp.finish_reason is None


# ===========================================================================
# 2. TestLLMClientInit
# ===========================================================================


class TestLLMClientInit:
    """Tests for LLMClient initialization."""

    def test_defaults_from_config(self) -> None:
        """LLMClient uses values from config when no args provided."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)  # clear cached singleton
            client = LLMClient()
            assert client.model == "gpt-oss-120b"
            assert client.temperature == 0.7
            assert client.max_tokens == 4000
            assert client.timeout == 60
            assert client.max_retries == 3

    def test_explicit_params_override_config(self) -> None:
        """Explicit constructor args override config defaults."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            client = LLMClient(
                model="yandexgpt-lite",
                temperature=0.3,
                max_tokens=1000,
                timeout=30,
                max_retries=1,
            )
            assert client.model == "yandexgpt-lite"
            assert client.temperature == 0.3
            assert client.max_tokens == 1000
            assert client.timeout == 30
            assert client.max_retries == 1

    def test_temperature_zero_not_overwritten(self) -> None:
        """temperature=0.0 must NOT be replaced by the config default (regression)."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            client = LLMClient(temperature=0.0)
            assert client.temperature == 0.0, (
                "Bug: temperature=0.0 was treated as falsy and replaced by config default"
            )

    def test_api_credentials_loaded(self) -> None:
        """api_key and folder_id come from Yandex config."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            client = LLMClient()
            assert client.api_key == ENV["YC_API_KEY"]
            assert client.folder_id == ENV["YC_FOLDER_ID"]

    def test_client_lazy_init(self) -> None:
        """HTTP client is not created until first access."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            llm = LLMClient()
            assert llm._client is None
            _ = llm.client  # triggers lazy creation
            assert llm._client is not None


# ===========================================================================
# 3. TestLLMClientComplete
# ===========================================================================


class TestLLMClientComplete:
    """Tests for LLMClient.complete()."""

    @pytest.fixture(autouse=True)
    def _reset_config(self) -> None:
        """Clear config singleton before each test."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)

    async def _make_client(self) -> LLMClient:
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            return LLMClient(max_retries=0)

    @pytest.mark.asyncio
    async def test_successful_text_response(self) -> None:
        """complete() returns LLMResponse with text content."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            client = LLMClient(max_retries=0)

            mock_http_resp = _make_httpx_response(MOCK_TEXT_RESPONSE)
            client._client = AsyncMock()
            client._client.post = AsyncMock(return_value=mock_http_resp)

            messages = [Message(role="user", content="Hello")]
            result = await client.complete(messages)

            assert isinstance(result, LLMResponse)
            assert result.content == "Hello, World!"
            assert result.tool_calls is None
            assert result.model == "gpt-oss-120b"
            assert result.finish_reason == "completed"

    @pytest.mark.asyncio
    async def test_text_response_token_usage(self) -> None:
        """complete() includes token usage in response."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            client = LLMClient(max_retries=0)

            mock_http_resp = _make_httpx_response(MOCK_TEXT_RESPONSE)
            client._client = AsyncMock()
            client._client.post = AsyncMock(return_value=mock_http_resp)

            result = await client.complete([Message(role="user", content="hi")])

            assert result.usage is not None
            assert result.usage.prompt_tokens == 10
            assert result.usage.completion_tokens == 5
            assert result.usage.total_tokens == 15

    @pytest.mark.asyncio
    async def test_tool_call_response(self) -> None:
        """complete() parses functionCall into tool_calls list."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            client = LLMClient(max_retries=0)

            mock_http_resp = _make_httpx_response(MOCK_TOOL_CALL_RESPONSE)
            client._client = AsyncMock()
            client._client.post = AsyncMock(return_value=mock_http_resp)

            result = await client.complete([Message(role="user", content="create issue")])

            assert result.content is None
            assert result.tool_calls is not None
            assert len(result.tool_calls) == 1
            tc = result.tool_calls[0]
            assert tc.name == "tracker_create_issue"
            assert tc.arguments == {"queue": "TEST", "summary": "Fix bug"}

    @pytest.mark.asyncio
    async def test_emulated_json_tool_call_response(self) -> None:
        """complete() converts an exact JSON tool envelope into a native tool call."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            client = LLMClient(max_retries=0)
            data = {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "tool": "tracker_find_issues",
                                        "arguments": {
                                            "assignee": "Roman Shinkarenko",
                                            "summary_hint": "",
                                        },
                                    }
                                ),
                            }
                        ],
                    }
                ],
                "status": "completed",
            }
            client._client = AsyncMock()
            client._client.post = AsyncMock(return_value=_make_httpx_response(data))

            result = await client.complete(
                [Message(role="user", content="найди мои задачи")],
                tools=[{"name": "tracker_find_issues", "parameters": {"type": "object"}}],
            )

            assert result.content is None
            assert result.tool_calls == [
                ToolCall(
                    name="tracker_find_issues",
                    arguments={"assignee": "Roman Shinkarenko", "summary_hint": ""},
                )
            ]

    @pytest.mark.asyncio
    async def test_unknown_emulated_tool_stays_text(self) -> None:
        """JSON must not execute unless its tool is present in the supplied schema list."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            client = LLMClient(max_retries=0)
            text = '{"tool":"delete_everything","arguments":{}}'
            data = {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                ],
                "status": "completed",
            }
            client._client = AsyncMock()
            client._client.post = AsyncMock(return_value=_make_httpx_response(data))

            result = await client.complete(
                [Message(role="user", content="hello")],
                tools=[{"name": "tracker_find_issues", "parameters": {"type": "object"}}],
            )

            assert result.content == text
            assert result.tool_calls is None

    @pytest.mark.asyncio
    async def test_retry_on_500_error(self) -> None:
        """complete() retries on 5xx server errors up to max_retries times."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            # 2 retries: first two calls → 500, third → success
            client = LLMClient(max_retries=2)

            error_resp = _make_httpx_response({}, status_code=500)
            ok_resp = _make_httpx_response(MOCK_TEXT_RESPONSE)

            client._client = AsyncMock()
            client._client.post = AsyncMock(side_effect=[error_resp, error_resp, ok_resp])

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await client.complete([Message(role="user", content="hi")])

            assert client._client.post.call_count == 3
            assert result.content == "Hello, World!"

    @pytest.mark.asyncio
    async def test_raises_llm_error_on_timeout(self) -> None:
        """complete() raises LLMError after all retries exhaust on timeout."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            client = LLMClient(max_retries=1)

            client._client = AsyncMock()
            client._client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(LLMError, match="timeout"):
                    await client.complete([Message(role="user", content="hi")])

    @pytest.mark.asyncio
    async def test_raises_llm_error_on_4xx(self) -> None:
        """complete() raises LLMError on 4xx (non-retryable) HTTP error."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            client = LLMClient(max_retries=3)

            error_resp = _make_httpx_response({}, status_code=401)
            client._client = AsyncMock()
            client._client.post = AsyncMock(return_value=error_resp)

            with pytest.raises(LLMError):
                await client.complete([Message(role="user", content="hi")])

            # Must NOT retry 4xx
            assert client._client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_dict_messages_accepted(self) -> None:
        """complete() accepts plain dicts in addition to Message objects."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            client = LLMClient(max_retries=0)

            mock_http_resp = _make_httpx_response(MOCK_TEXT_RESPONSE)
            client._client = AsyncMock()
            client._client.post = AsyncMock(return_value=mock_http_resp)

            result = await client.complete([{"role": "user", "text": "Hello"}])
            assert result.content == "Hello, World!"

    @pytest.mark.asyncio
    async def test_latency_ms_is_positive(self) -> None:
        """LLMResponse.latency_ms reflects elapsed time."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            client = LLMClient(max_retries=0)

            mock_http_resp = _make_httpx_response(MOCK_TEXT_RESPONSE)
            client._client = AsyncMock()
            client._client.post = AsyncMock(return_value=mock_http_resp)

            result = await client.complete([Message(role="user", content="hi")])
            assert result.latency_ms >= 0


# ===========================================================================
# 4. TestStreamComplete
# ===========================================================================


class TestStreamComplete:
    """Tests for LLMClient.stream_complete()."""

    @pytest.mark.asyncio
    async def test_stream_complete_is_async_generator(self) -> None:
        """stream_complete() returns an async generator."""
        import inspect

        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            client = LLMClient(max_retries=0)

            mock_http_resp = _make_httpx_response(MOCK_TEXT_RESPONSE)
            client._client = AsyncMock()
            client._client.post = AsyncMock(return_value=mock_http_resp)

            gen = client.stream_complete([Message(role="user", content="hi")])
            assert inspect.isasyncgen(gen)
            # Consume it to avoid ResourceWarning
            with patch("asyncio.sleep", new_callable=AsyncMock):
                chunks = [chunk async for chunk in gen]
            assert len(chunks) > 0

    @pytest.mark.asyncio
    async def test_stream_complete_yields_chars(self) -> None:
        """stream_complete() yields individual characters from content."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            client = LLMClient(max_retries=0)

            mock_http_resp = _make_httpx_response(MOCK_TEXT_RESPONSE)
            client._client = AsyncMock()
            client._client.post = AsyncMock(return_value=mock_http_resp)

            with patch("asyncio.sleep", new_callable=AsyncMock):
                chunks = [
                    chunk
                    async for chunk in client.stream_complete([Message(role="user", content="hi")])
                ]

            assert "".join(chunks) == "Hello, World!"

    @pytest.mark.asyncio
    async def test_stream_complete_empty_for_tool_call(self) -> None:
        """stream_complete() yields nothing when response has a tool_call."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)
            client = LLMClient(max_retries=0)

            mock_http_resp = _make_httpx_response(MOCK_TOOL_CALL_RESPONSE)
            client._client = AsyncMock()
            client._client.post = AsyncMock(return_value=mock_http_resp)

            with patch("asyncio.sleep", new_callable=AsyncMock):
                chunks = [
                    chunk
                    async for chunk in client.stream_complete(
                        [Message(role="user", content="create task")]
                    )
                ]

            assert chunks == []


# ===========================================================================
# 5. TestCompleteConvenience
# ===========================================================================


class TestCompleteConvenience:
    """Tests for module-level complete() convenience function."""

    @pytest.mark.asyncio
    async def test_convenience_complete_returns_llm_response(self) -> None:
        """Module-level complete() returns LLMResponse."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)

            with patch("core.llm.LLMClient") as MockClient:
                instance = AsyncMock()
                instance.complete = AsyncMock(
                    return_value=LLMResponse(
                        content="Hello, World!",
                        model="yandexgpt-pro",
                        latency_ms=50,
                        finish_reason="COMPLETED",
                        usage=TokenUsage(
                            prompt_tokens=10,
                            completion_tokens=5,
                            total_tokens=15,
                        ),
                    )
                )
                instance.close = AsyncMock()
                MockClient.return_value = instance

                result = await complete([Message(role="user", content="hi")])

            assert isinstance(result, LLMResponse)
            assert result.content == "Hello, World!"

    @pytest.mark.asyncio
    async def test_convenience_complete_closes_client(self) -> None:
        """Module-level complete() always closes the HTTP client."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)

            with patch("core.llm.LLMClient") as MockClient:
                instance = AsyncMock()
                instance.complete = AsyncMock(
                    return_value=LLMResponse(content="ok", model="yandexgpt-pro")
                )
                instance.close = AsyncMock()
                MockClient.return_value = instance

                await complete([Message(role="user", content="hi")])

            instance.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_convenience_complete_closes_client_on_error(self) -> None:
        """Module-level complete() closes the client even when complete() raises."""
        with patch.dict("os.environ", ENV):
            from core.config import set_config

            set_config(None)

            with patch("core.llm.LLMClient") as MockClient:
                instance = AsyncMock()
                instance.complete = AsyncMock(
                    side_effect=LLMError("Request timeout after 3 retries")
                )
                instance.close = AsyncMock()
                MockClient.return_value = instance

                with pytest.raises(LLMError):
                    await complete([Message(role="user", content="hi")])

            instance.close.assert_called_once()
