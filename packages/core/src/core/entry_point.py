"""
EntryPoint — universal dispatcher for a bot's request handling.

Supports two modes:

* **Agent mode** — wraps a single :class:`~core.agent.BaseAgent` instance.
* **Menu mode** — routes ``/<command>`` messages to named agents.

Example (agent mode)::

    ep = EntryPoint(WeatherAgent())
    result = await ep.invoke("What is the weather in Moscow?")

Example (menu mode)::

    ep = EntryPoint({
        "weather": WeatherAgent(),
        "tasks":   TaskAgent(),
    })
    result = await ep.invoke("/weather What is the weather in Moscow?")
"""

from __future__ import annotations

import logging
from typing import Any

from core.agent import AgentResponse, BaseAgent
from core.exceptions import AgentError
from core.llm import Message

logger = logging.getLogger(__name__)

_MENU_HELP_TEMPLATE = "Available commands:\n{commands}\n\nSend /<command> <message> to start."


class EntryPoint:
    """Dispatches user messages to the appropriate agent.

    Parameters
    ----------
    config:
        Either a :class:`~core.agent.BaseAgent` instance (agent mode) or a
        ``dict[str, BaseAgent]`` mapping command names to agents (menu mode).
    """

    def __init__(self, config: BaseAgent | dict[str, BaseAgent]) -> None:
        if isinstance(config, BaseAgent):
            self._mode = "agent"
            self._agent: BaseAgent = config
            self._commands: dict[str, BaseAgent] = {}
        elif isinstance(config, dict):
            if not config:
                raise AgentError("EntryPoint menu config must not be empty")
            self._mode = "menu"
            self._agent = next(iter(config.values()))  # default (first) agent
            self._commands = dict(config)
        else:
            raise AgentError(
                f"EntryPoint config must be a BaseAgent or dict, got {type(config).__name__}"
            )

    @property
    def mode(self) -> str:
        """Return ``'agent'`` or ``'menu'``."""
        return self._mode

    @property
    def commands(self) -> dict[str, BaseAgent]:
        """Return the command → agent mapping (empty in agent mode)."""
        return dict(self._commands)

    def _help_text(self) -> str:
        lines = [f"  /{cmd} — {agent.description}" for cmd, agent in self._commands.items()]
        return _MENU_HELP_TEMPLATE.format(commands="\n".join(lines))

    async def invoke(
        self,
        message: str,
        *,
        history: list[Message] | None = None,
        prompt_vars: dict[str, Any] | None = None,
    ) -> AgentResponse:
        """Handle a user message and return an :class:`~core.agent.AgentResponse`.

        Parameters
        ----------
        message:
            Raw user text.
        history:
            Optional prior conversation turns prepended to the user message.
        prompt_vars:
            Variables substituted into the agent's system prompt.
        """
        if self._mode == "agent":
            return await self._invoke_agent(self._agent, message, history, prompt_vars)

        # Menu mode
        stripped = message.strip()
        if stripped in ("/help", "/start"):
            return AgentResponse(content=self._help_text(), model_used="")

        if stripped.startswith("/"):
            parts = stripped[1:].split(None, 1)
            command = parts[0].lower()
            user_text = parts[1] if len(parts) > 1 else ""
            if command not in self._commands:
                return AgentResponse(
                    content=f"Unknown command '/{command}'.\n\n{self._help_text()}",
                    model_used="",
                )
            return await self._invoke_agent(
                self._commands[command], user_text, history, prompt_vars
            )

        # No command prefix — route to the default (first) agent
        return await self._invoke_agent(self._agent, stripped, history, prompt_vars)

    async def _invoke_agent(
        self,
        agent: BaseAgent,
        message: str,
        history: list[Message] | None,
        prompt_vars: dict[str, Any] | None,
    ) -> AgentResponse:
        messages: list[Message] = list(history or [])
        if message:
            messages.append(Message(role="user", content=message))
        if not messages:
            raise AgentError("Cannot invoke agent with empty message and empty history")
        logger.debug("EntryPoint: routing to agent '%s'", agent.name)
        return await agent.run(messages, prompt_vars=prompt_vars)


__all__ = [
    "EntryPoint",
]
