"""
LLM integration supporting multiple providers.

Supported providers:
- ``yandex``: Yandex Cloud OpenAI-compatible Responses API
  (``instructions`` + ``input`` items, ``output`` items).
- ``openrouter``: OpenRouter Chat Completions API
  (standard ``messages`` / ``choices`` format).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from core.config import get_config
from core.exceptions import LLMError
from core.metrics import llm_latency_seconds, llm_requests_total, llm_tokens_total

# Provider-specific endpoints.
_YANDEX_RESPONSES_URL = "https://ai.api.cloud.yandex.net/v1/responses"


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
    LLM client supporting multiple providers.

    Features:
    - Yandex Cloud OpenAI-compatible Responses API
    - OpenRouter Chat Completions API (Gemini, Claude, etc.)
    - Tool calling support (both native and emulated)
    - Streaming responses (client-side char chunking)
    - Automatic retries with exponential backoff
    - Token usage tracking
    """

    def __init__(
        self,
        model: str | None = None,
        provider: Literal["yandex", "openrouter"] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
    ):
        config = get_config()
        llm_cfg = config.llm
        self.provider = provider or "yandex"

        if self.provider == "openrouter":
            self.model = model if model is not None else llm_cfg.openrouter_default_model
            self.temperature = temperature if temperature is not None else 0.7
            self.max_tokens = max_tokens if max_tokens is not None else 4000
            self.timeout = timeout if timeout is not None else 60
            self.max_retries = max_retries if max_retries is not None else 3
            self.api_key = llm_cfg.openrouter_api_key
            self.base_url = llm_cfg.openrouter_base_url
            self.folder_id = ""
        else:
            self.model = model if model is not None else llm_cfg.yandexgpt_model
            self.temperature = temperature if temperature is not None else llm_cfg.yandexgpt_temperature
            self.max_tokens = max_tokens if max_tokens is not None else llm_cfg.yandexgpt_max_tokens
            self.timeout = timeout if timeout is not None else llm_cfg.yandexgpt_timeout
            self.max_retries = max_retries if max_retries is not None else llm_cfg.yandexgpt_max_retries
            self.api_key = config.yandex.yc_api_key
            self.folder_id = config.yandex.yc_folder_id
            self.base_url = ""

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
                if self.provider == "openrouter":
                    return await self._complete_openrouter(
                        messages, tools, start_time, **kwargs
                    )
                return await self._complete_yandex(
                    messages, tools, stream, start_time, **kwargs
                )
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

    # ── Yandex (Responses API) ────────────────────────────────────────

    async def _complete_yandex(
        self,
        messages: list[Message | dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
        start_time: float,
        **kwargs: Any,
    ) -> LLMResponse:
        """Yandex Cloud OpenAI-compatible Responses API."""
        instructions, input_items = self._split_messages(messages)

        request_body: dict[str, Any] = {
            "model": f"gpt://{self.folder_id}/{self.model}/latest",
            "input": input_items,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_output_tokens": kwargs.get("max_tokens", self.max_tokens),
        }
        if instructions:
            request_body["instructions"] = instructions
        if tools:
            # OpenAI Responses function tools are flattened (not nested under "function")
            request_body["tools"] = [{"type": "function", **tool} for tool in tools]

        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "OpenAI-Project": self.folder_id,
            "Content-Type": "application/json",
        }

        response = await self.client.post(_YANDEX_RESPONSES_URL, headers=headers, json=request_body)
        response.raise_for_status()

        data = response.json()
        latency_ms = int((time.monotonic() - start_time) * 1000)

        content, tool_calls, finish_reason = self._parse_yandex_output(data)
        if not tool_calls and content and tools:
            emulated_call = self._parse_emulated_tool_call(content, tools)
            if emulated_call is not None:
                content = None
                tool_calls = [emulated_call]
        usage = self._parse_yandex_usage(data)

        self._record_metrics(usage, latency_ms)
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            model=self.model,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
        )

    # ── OpenRouter (Chat Completions API) ──────────────────────────────

    async def _complete_openrouter(
        self,
        messages: list[Message | dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        start_time: float,
        **kwargs: Any,
    ) -> LLMResponse:
        """OpenRouter Chat Completions API."""
        chat_messages = self._messages_to_chat(messages)

        request_body: dict[str, Any] = {
            "model": self.model,
            "messages": chat_messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
        }
        if tools:
            # OpenRouter expects OpenAI-style nested function tools:
            # {"type":"function","function":{"name":...,"parameters":...}}
            request_body["tools"] = self._tools_to_openrouter(tools)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self.base_url}/chat/completions"
        response = await self.client.post(url, headers=headers, json=request_body)
        response.raise_for_status()

        data = response.json()
        latency_ms = int((time.monotonic() - start_time) * 1000)

        content, tool_calls, finish_reason = self._parse_openrouter_output(data)
        if not tool_calls and content and tools:
            emulated_call = self._parse_emulated_tool_call(content, tools)
            if emulated_call is not None:
                content = None
                tool_calls = [emulated_call]
        usage = self._parse_openrouter_usage(data)

        self._record_metrics(usage, latency_ms)
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            model=self.model,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
        )

    # ── Message conversion helpers ────────────────────────────────────

    @staticmethod
    def _messages_to_chat(
        messages: list[Message | dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Convert messages to OpenAI Chat Completions format.

        System messages become regular ``{"role":"system","content":...}``
        items (no separate ``instructions`` field).
        """
        items: list[dict[str, str]] = []
        for msg in messages:
            if isinstance(msg, Message):
                role, content = msg.role, msg.content
            else:
                role = msg.get("role", "user")
                content = msg.get("content", msg.get("text", ""))
            content = str(content or "").strip()
            if content:
                items.append({"role": role, "content": content})
        return items

    @staticmethod
    def _split_messages(
        messages: list[Message | dict[str, Any]],
    ) -> tuple[str, list[dict[str, str]]]:
        """Split chat messages into Responses-API ``instructions`` + ``input``.

        System messages are concatenated into ``instructions``; the rest become
        ``input`` items ``{"role", "content"}``. Plain dicts may use ``content``
        or the legacy ``text`` key.
        """
        instructions_parts: list[str] = []
        input_items: list[dict[str, str]] = []
        for msg in messages:
            if isinstance(msg, Message):
                role, content = msg.role, msg.content
            else:
                role = msg.get("role", "user")
                content = msg.get("content", msg.get("text", ""))
            content = str(content or "").strip()
            if role == "system":
                if content:
                    instructions_parts.append(content)
            elif content:
                input_items.append({"role": role, "content": content})
        return "\n\n".join(instructions_parts), input_items

    @staticmethod
    def _tools_to_openrouter(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert flat tool definitions to OpenRouter nested function format.

        Our internal tools are ``{"name":..., "description":..., "parameters":...}``.
        OpenRouter expects ``{"type":"function","function":{"name":...,"parameters":...}}``.
        """
        result: list[dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") == "function" and "function" in tool:
                # Already in nested format — pass through.
                result.append(tool)
                continue
            func_def: dict[str, Any] = {}
            if "name" in tool:
                func_def["name"] = tool["name"]
            if "description" in tool:
                func_def["description"] = tool["description"]
            if "parameters" in tool:
                func_def["parameters"] = tool["parameters"]
            result.append({"type": "function", "function": func_def})
        return result

    # ── Yandex response parsing ───────────────────────────────────────

    @staticmethod
    def _parse_yandex_output(
        data: dict[str, Any],
    ) -> tuple[str | None, list[ToolCall] | None, str | None]:
        """Parse Responses-API ``output`` into (content, tool_calls, finish_reason)."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for item in data.get("output", []):
            item_type = item.get("type")
            if item_type == "function_call":
                raw_args = item.get("arguments", {})
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except json.JSONDecodeError:
                        args = {}
                else:
                    args = raw_args or {}
                tool_calls.append(ToolCall(name=item.get("name", ""), arguments=args))
            elif item_type == "message":
                for part in item.get("content", []):
                    if part.get("type") in ("output_text", "text"):
                        text_parts.append(part.get("text", ""))

        content: str | None = None
        if not tool_calls:
            content = "".join(text_parts) if text_parts else data.get("output_text", "")

        finish_reason = data.get("status", "completed")
        return content, (tool_calls or None), finish_reason

    @staticmethod
    def _parse_yandex_usage(data: dict[str, Any]) -> TokenUsage:
        """Parse Responses-API ``usage`` (input_tokens / output_tokens / total_tokens)."""
        u = data.get("usage", {})
        return TokenUsage(
            prompt_tokens=int(u.get("input_tokens", 0) or 0),
            completion_tokens=int(u.get("output_tokens", 0) or 0),
            total_tokens=int(u.get("total_tokens", 0) or 0),
        )

    # ── OpenRouter response parsing ────────────────────────────────────

    @staticmethod
    def _parse_openrouter_output(
        data: dict[str, Any],
    ) -> tuple[str | None, list[ToolCall] | None, str | None]:
        """Parse OpenRouter Chat Completions response."""
        choices = data.get("choices", [])
        if not choices:
            return None, None, "empty"

        choice = choices[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "stop")

        # Content
        content: str | None = message.get("content") or None

        # Tool calls
        raw_tool_calls = message.get("tool_calls")
        tool_calls: list[ToolCall] | None = None
        if raw_tool_calls:
            parsed: list[ToolCall] = []
            for tc in raw_tool_calls:
                func = tc.get("function", {})
                raw_args = func.get("arguments", "{}")
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except json.JSONDecodeError:
                        args = {}
                else:
                    args = raw_args or {}
                parsed.append(ToolCall(name=func.get("name", ""), arguments=args))
            tool_calls = parsed or None

        return content, tool_calls, finish_reason

    @staticmethod
    def _parse_openrouter_usage(data: dict[str, Any]) -> TokenUsage:
        """Parse OpenRouter usage."""
        u = data.get("usage", {})
        return TokenUsage(
            prompt_tokens=int(u.get("prompt_tokens", 0) or 0),
            completion_tokens=int(u.get("completion_tokens", 0) or 0),
            total_tokens=int(u.get("total_tokens", 0) or 0),
        )

    # ── Shared helpers ─────────────────────────────────────────────────

    @staticmethod
    def _parse_emulated_tool_call(
        content: str,
        tools: list[dict[str, Any]],
    ) -> ToolCall | None:
        """Parse a model-emitted JSON tool call when native function calling is skipped."""
        raw = content.strip()
        if raw.startswith("```") and raw.endswith("```"):
            lines = raw.splitlines()
            if len(lines) < 3 or lines[0].strip() not in ("```", "```json"):
                return None
            raw = "\n".join(lines[1:-1]).strip()

        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

        if not isinstance(payload, dict) or not set(payload).issubset({"tool", "arguments"}):
            return None
        tool_name = payload.get("tool")
        arguments = payload.get("arguments", {})
        allowed_tools = {tool.get("name") for tool in tools}
        if not isinstance(tool_name, str) or tool_name not in allowed_tools:
            return None
        if not isinstance(arguments, dict):
            return None
        return ToolCall(name=tool_name, arguments=arguments)

    def _record_metrics(self, usage: TokenUsage, latency_ms: int) -> None:
        """Record Prometheus metrics for a successful request."""
        llm_requests_total.labels(model=self.model, status="success").inc()
        llm_latency_seconds.labels(model=self.model).observe(latency_ms / 1000)
        if usage.prompt_tokens:
            llm_tokens_total.labels(model=self.model, token_type="prompt").inc(usage.prompt_tokens)
        if usage.completion_tokens:
            llm_tokens_total.labels(model=self.model, token_type="completion").inc(
                usage.completion_tokens
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
