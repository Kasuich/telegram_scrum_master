"""
Global bot registry with auto-registration support.

Bots register themselves on instantiation (via :class:`~core.bot.BaseBot`).
The registry is a process-wide singleton — no database needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.exceptions import RegistryError

if TYPE_CHECKING:
    from core.bot import BaseBot


class BotRegistry:
    """Singleton registry for :class:`~core.bot.BaseBot` instances.

    Populated automatically when a ``BaseBot`` is instantiated.
    """

    _instance: BotRegistry | None = None
    _bots: dict[str, BaseBot]

    def __new__(cls) -> BotRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._bots = {}
        return cls._instance

    def register(self, bot: BaseBot) -> None:
        """Register a bot. Raises :class:`~core.exceptions.RegistryError` on duplicate."""
        if bot.bot_id in self._bots:
            raise RegistryError(f"Bot already registered: '{bot.bot_id}'")
        self._bots[bot.bot_id] = bot

    def get(self, bot_id: str) -> BaseBot:
        """Return bot by ID. Raises :class:`~core.exceptions.RegistryError` if absent."""
        if bot_id not in self._bots:
            raise RegistryError(f"Bot not found: '{bot_id}'")
        return self._bots[bot_id]

    def list_all(self) -> list[BaseBot]:
        """Return all registered bots."""
        return list(self._bots.values())

    def list_for_platform(self, platform: str) -> list[BaseBot]:
        """Return bots available on the given platform."""
        return [bot for bot in self._bots.values() if platform in bot.platforms]

    def exists(self, bot_id: str) -> bool:
        """Return True if a bot with this ID is registered."""
        return bot_id in self._bots

    def unregister(self, bot_id: str) -> None:
        """Remove a bot from the registry (mainly for tests)."""
        self._bots.pop(bot_id, None)

    def clear(self) -> None:
        """Remove all bots (mainly for tests)."""
        self._bots.clear()


def get_bot_registry() -> BotRegistry:
    """Return the global :class:`BotRegistry` singleton."""
    return BotRegistry()


__all__ = [
    "BotRegistry",
    "get_bot_registry",
]
