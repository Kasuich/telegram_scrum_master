"""
BaseBot — top-level unit that groups an EntryPoint with platform metadata.

Creating a ``BaseBot`` instance automatically registers it in the global
:class:`~core.registry.BotRegistry`, so no explicit registration call is needed.

Example::

    from core.agent import BaseAgent, LLMSettings
    from core.bot import BaseBot
    from core.entry_point import EntryPoint

    class HelpAgent(BaseAgent):
        name = "help_agent"
        description = "Answers help questions"
        prompt = "You are a helpful assistant."
        llm_configs = [LLMSettings(model="yandexgpt-lite")]

    MY_BOT = BaseBot(
        bot_id="help_bot",
        name="Help Bot",
        description="A simple help bot",
        entry_point=EntryPoint(HelpAgent()),
        platforms=["web", "telegram"],
    )
"""

from __future__ import annotations

from core.entry_point import EntryPoint
from core.registry import BotRegistry


class BaseBot:
    """Bot definition with platform metadata and auto-registration.

    Parameters
    ----------
    bot_id:
        Unique identifier (slug, e.g. ``"pm_bot"``).
    name:
        Human-readable name.
    entry_point:
        :class:`~core.entry_point.EntryPoint` wrapping the agent(s).
    platforms:
        List of platform identifiers where this bot is active
        (e.g. ``["web", "telegram", "a2a"]``).
    description:
        Optional description shown in bot listings.
    """

    def __init__(
        self,
        *,
        bot_id: str,
        name: str,
        entry_point: EntryPoint,
        platforms: list[str] | None = None,
        description: str = "",
    ) -> None:
        self.bot_id = bot_id
        self.name = name
        self.description = description
        self.entry_point = entry_point
        self.platforms: list[str] = platforms or []

        BotRegistry().register(self)

    def __repr__(self) -> str:
        return (
            f"BaseBot(bot_id={self.bot_id!r}, name={self.name!r}, "
            f"platforms={self.platforms!r})"
        )


__all__ = [
    "BaseBot",
]
