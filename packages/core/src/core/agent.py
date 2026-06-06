"""
Code-first agent framework.

Usage example::

    from core.agent import BaseAgent, LLMSettings
    from core.tools import platform_tool

    @platform_tool(name="get_weather", risk="low")
    async def get_weather(city: str) -> dict:
        "Get weather for a city."
        return {"temp": 20}

    class WeatherAgent(BaseAgent):
        name = "weather_agent"
        description = "Answers weather questions"
        prompt = "You are a weather assistant."
        tools = ["get_weather"]
        llm_configs = [
            LLMSettings(model="gpt-oss-120b", temperature=0.3),
            LLMSettings(model="yandexgpt-lite", temperature=0.3),   # fallback
        ]

    response = await WeatherAgent().run(
        [Message(role="user", content="What's the weather in Moscow?")]
    )
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from core.exceptions import AgentError, LLMError
from core.llm import LLMClient, LLMResponse, Message, TokenUsage
from core.tools import get_registry

logger = logging.getLogger(__name__)


class LLMSettings(BaseModel):
    """Per-agent LLM configuration.

    Multiple instances can be provided as a fallback chain — the agent tries
    each in order and moves to the next on failure.
    """

    model: str = "gpt-oss-120b"
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: int | None = None
    max_retries: int | None = None

    def to_client_kwargs(self) -> dict[str, Any]:
        """Return kwargs suitable for LLMClient constructor."""
        kwargs: dict[str, Any] = {"model": self.model}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if self.timeout is not None:
            kwargs["timeout"] = self.timeout
        if self.max_retries is not None:
            kwargs["max_retries"] = self.max_retries
        return kwargs


class AgentResponse(BaseModel):
    """Result of a single agent turn."""

    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    usage: TokenUsage | None = None
    model_used: str = ""
    llm_attempts: int = Field(default=1, description="How many LLM configs were tried")


class BaseAgent:
    """Base class for code-first agents.

    Subclasses declare their configuration as class attributes.
    No database insert required — instantiation is enough.

    Class attributes
    ----------------
    name : str
        Unique identifier for this agent (snake_case).
    description : str
        Human-readable description shown to users and sub-agent callers.
    prompt : str
        System prompt sent to the LLM. Supports ``{variable}`` placeholders
        substituted via ``prompt_vars`` argument of :meth:`run`.
    tools : list[str]
        Names of :func:`~core.tools.platform_tool`-registered tools available
        to this agent. Unknown names are skipped with a warning.
    llm_configs : list[LLMSettings]
        Ordered list of LLM configs. The agent tries them in sequence,
        falling back to the next on :class:`~core.exceptions.LLMError`.
    action_only : bool
        When True, the agent must prefer tool calls over chat; final ``reply``
        is built from executed tool results, not conversational LLM prose.
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    prompt: ClassVar[str] = ""
    tools: ClassVar[list[str]] = []
    llm_configs: ClassVar[list[LLMSettings]] = []
    action_only: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.name:
            raise AgentError(f"Agent class {cls.__name__} must define a non-empty 'name'")

    def _build_system_message(self, prompt_vars: dict[str, Any] | None) -> Message:
        """Render system prompt with optional variable substitution."""
        text = self.prompt
        if prompt_vars is not None:
            try:
                text = text.format(**prompt_vars)
            except KeyError as e:
                raise AgentError(f"Missing prompt variable: {e}") from e
        return Message(role="system", content=text)

    def _resolve_tool_schemas(self) -> list[dict[str, Any]]:
        """Return OpenAPI schemas for declared tools that exist in registry."""
        if not self.tools:
            return []
        registry = get_registry()
        schemas = []
        for tool_name in self.tools:
            if registry.exists(tool_name):
                schemas.append(registry.get(tool_name).get_schema())
            else:
                logger.warning("Agent '%s': tool '%s' not found in registry", self.name, tool_name)
        return schemas

    def _effective_llm_configs(self) -> list[LLMSettings]:
        """Return llm_configs or a default single-config list."""
        if self.llm_configs:
            return list(self.llm_configs)
        return [LLMSettings()]

    async def _call_with_fallback(
        self,
        messages: list[Message],
        tool_schemas: list[dict[str, Any]],
    ) -> tuple[LLMResponse, int]:
        """Try each LLMSettings in order, return (response, attempt_index+1)."""
        configs = self._effective_llm_configs()
        last_error: LLMError | None = None

        for idx, llm_cfg in enumerate(configs):
            client = LLMClient(**llm_cfg.to_client_kwargs())
            try:
                response = await client.complete(messages, tools=tool_schemas or None)
                logger.debug(
                    "Agent '%s' used model '%s' (attempt %d/%d)",
                    self.name,
                    llm_cfg.model,
                    idx + 1,
                    len(configs),
                )
                return response, idx + 1
            except LLMError as exc:
                last_error = exc
                logger.warning(
                    "Agent '%s': model '%s' failed (attempt %d/%d): %s",
                    self.name,
                    llm_cfg.model,
                    idx + 1,
                    len(configs),
                    exc,
                )
            finally:
                await client.close()

        detail = f": {last_error}" if last_error else ""
        raise AgentError(
            f"Agent '{self.name}': all {len(configs)} LLM config(s) failed{detail}"
        ) from last_error

    async def run(
        self,
        messages: list[Message],
        *,
        prompt_vars: dict[str, Any] | None = None,
        extra_tool_schemas: list[dict[str, Any]] | None = None,
    ) -> AgentResponse:
        """Execute one agent turn.

        Parameters
        ----------
        messages:
            Conversation history (system message is prepended automatically).
        prompt_vars:
            Optional mapping for ``{variable}`` placeholders in :attr:`prompt`.
        extra_tool_schemas:
            Additional tool schemas merged with the agent's own tools.

        Returns
        -------
        AgentResponse
            Contains either ``content`` (text) or ``tool_calls``.
        """
        if not self.prompt:
            raise AgentError(f"Agent '{self.name}' has no prompt defined")

        system_message = self._build_system_message(prompt_vars)
        full_messages = [system_message, *messages]

        tool_schemas = self._resolve_tool_schemas()
        if extra_tool_schemas:
            tool_schemas = tool_schemas + extra_tool_schemas

        response, attempts = await self._call_with_fallback(full_messages, tool_schemas)

        tool_calls_out: list[dict[str, Any]] | None = None
        if response.tool_calls:
            tool_calls_out = [
                {"name": tc.name, "arguments": tc.arguments} for tc in response.tool_calls
            ]

        return AgentResponse(
            content=response.content,
            tool_calls=tool_calls_out,
            usage=response.usage,
            model_used=response.model,
            llm_attempts=attempts,
        )


__all__ = [
    "LLMSettings",
    "AgentResponse",
    "BaseAgent",
]
