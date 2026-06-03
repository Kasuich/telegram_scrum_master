"""
LLM integration with LiteLLM and YandexGPT support.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from core.config import get_config
from core.exceptions import LLMError
from core.metrics import llm_latency_seconds, llm_requests_total, llm_tokens_total


class Message(BaseModel):
    """Chat message."""

    role: Literal["system", "user", "assistant"]
    content: str


class ToolCall(BaseModel):
    """Tool call from LLM."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallFunction(BaseModel):
    """OpenAI-style function call."""

    name: str
    arguments: str


class ToolCallDelta(BaseModel):
    """Streaming tool call delta."""

    function: ToolCallFunction


class TokenUsage(BaseModel):
    """Token usage statistics."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    """Response from LLM completion."""

    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    usage: TokenUsage | None = None
    model: str
    latency_ms: int = 0
    finish_reason: str | None = None


class LLMClient:
    """
    LLM client wrapper for YandexGPT via LiteLLM.

    Features:
    - Tool calling support
    - Streaming responses
    - Automatic retries with exponential backoff
    - Token usage tracking
    """

    def __init__(
        self,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
    ):
        config = get_config()
        llm_cfg = config.llm
        self.model = model if model is not None else llm_cfg.yandexgpt_model
        self.temperature = temperature if temperature is not None else llm_cfg.yandexgpt_temperature
        self.max_tokens = max_tokens if max_tokens is not None else llm_cfg.yandexgpt_max_tokens
        self.timeout = timeout if timeout is not None else llm_cfg.yandexgpt_timeout
        self.max_retries = max_retries if max_retries is not None else llm_cfg.yandexgpt_max_retries
        self.api_key = config.yandex.yc_api_key
        self.folder_id = config.yandex.yc_folder_id
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def complete(
        self,
        messages: list[Message | dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Send completion request to LLM.

        Args:
            messages: List of chat messages
            tools: Optional list of available tools
            stream: Whether to stream response
            **kwargs: Additional model parameters

        Returns:
            LLMResponse with content or tool_calls
        """
        start_time = time.monotonic()

        for attempt in range(self.max_retries + 1):
            try:
                return await self._complete_impl(messages, tools, stream, start_time, **kwargs)
            except httpx.TimeoutException as e:
                if attempt == self.max_retries:
                    llm_requests_total.labels(model=self.model, status="error").inc()
                    raise LLMError(f"Request timeout after {self.max_retries} retries") from e
                await asyncio.sleep(2**attempt * 0.5)
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500 and attempt < self.max_retries:
                    await asyncio.sleep(2**attempt * 0.5)
                    continue
                llm_requests_total.labels(model=self.model, status="error").inc()
                raise LLMError(f"HTTP {e.response.status_code}: {e.response.text}") from e

        llm_requests_total.labels(model=self.model, status="error").inc()
        raise LLMError("Max retries exceeded")

    async def _complete_impl(
        self,
        messages: list[Message | dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
        start_time: float,
        **kwargs: Any,
    ) -> LLMResponse:
        """Internal completion implementation (foundationModels v1 API)."""
        normalized_messages = []
        for msg in messages:
            if isinstance(msg, Message):
                normalized_messages.append({"role": msg.role, "text": msg.content})
            else:
                normalized_messages.append(msg)

        request_body: dict[str, Any] = {
            "modelUri": f"gpt://{self.folder_id}/{self.model}/latest",
            "completionOptions": {
                "stream": stream,
                "temperature": kwargs.get("temperature", self.temperature),
                "maxTokens": str(kwargs.get("max_tokens", self.max_tokens)),
            },
            "messages": normalized_messages,
        }

        if tools:
            request_body["tools"] = [{"function": tool} for tool in tools]

        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json",
        }

        response = await self.client.post(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
            headers=headers,
            json=request_body,
        )
        response.raise_for_status()

        data = response.json()
        latency_ms = int((time.monotonic() - start_time) * 1000)

        # foundationModels v1 response: result.alternatives[0].message
        result = data.get("result", {})
        alternatives = result.get("alternatives", [])
        alt = alternatives[0] if alternatives else {}
        result_message = alt.get("message", {})
        finish_reason = alt.get("status", "ALTERNATIVE_STATUS_FINAL")

        tool_calls: list[ToolCall] | None = None
        content: str | None = None

        # Tool call response: message.toolCallList.toolCalls[].functionCall
        tool_call_list = result_message.get("toolCallList", {})
        raw_tool_calls = tool_call_list.get("toolCalls", [])
        if raw_tool_calls:
            tool_calls = [
                ToolCall(
                    name=tc.get("functionCall", {}).get("name", ""),
                    arguments=tc.get("functionCall", {}).get("arguments", {}),
                )
                for tc in raw_tool_calls
            ]
        else:
            content = result_message.get("text", "")

        # Usage: fields are strings in v1 API
        usage_data = result.get("usage", {})
        usage = TokenUsage(
            prompt_tokens=int(usage_data.get("inputTokens", 0)),
            completion_tokens=int(usage_data.get("completionTokens", 0)),
            total_tokens=int(usage_data.get("totalTokens", 0)),
        )

        llm_requests_total.labels(model=self.model, status="success").inc()
        llm_latency_seconds.labels(model=self.model).observe(latency_ms / 1000)
        if usage.prompt_tokens:
            llm_tokens_total.labels(model=self.model, token_type="prompt").inc(usage.prompt_tokens)
        if usage.completion_tokens:
            llm_tokens_total.labels(model=self.model, token_type="completion").inc(
                usage.completion_tokens
            )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            model=self.model,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
        )

    async def stream_complete(
        self,
        messages: list[Message | dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """
        Stream completion from LLM.

        Args:
            messages: List of chat messages
            tools: Optional list of available tools
            **kwargs: Additional model parameters

        Yields:
            Response chunks as strings
        """
        response = await self.complete(messages, tools, stream=True, **kwargs)

        if response.content:
            for char in response.content:
                yield char
                await asyncio.sleep(0.01)


async def complete(
    messages: list[Message | dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> LLMResponse:
    """
    Convenience function for LLM completion.

    Args:
        messages: List of chat messages
        tools: Optional list of available tools
        **kwargs: Additional parameters

    Returns:
        LLMResponse from default client
    """
    client = LLMClient()
    try:
        return await client.complete(messages, tools, **kwargs)
    finally:
        await client.close()


__all__ = [
    "Message",
    "ToolCall",
    "TokenUsage",
    "LLMResponse",
    "LLMError",
    "LLMClient",
    "complete",
]
