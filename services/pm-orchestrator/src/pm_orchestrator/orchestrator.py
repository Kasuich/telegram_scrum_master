"""
OrchestratorService — discovers agents, manages runners, handles invoke/resume.

Agent auto-discovery:
  Any module inside the ``agents/`` package that defines a subclass of
  :class:`~core.agent.BaseAgent` is automatically registered on startup.
  To add a new agent, just create ``agents/my_agent.py`` with a BaseAgent
  subclass — no other registration required.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import Any

from core.agent import BaseAgent
from core.config import RuntimeConfig
from core.react import AgentResult, ReActRunner

logger = logging.getLogger(__name__)


class OrchestratorService:
    """Central service that owns all agent runners."""

    def __init__(self) -> None:
        self._runners: dict[str, ReActRunner] = {}
        # global confirm_id → agent_name index (runners share memory stores)
        self._confirm_index: dict[str, str] = {}
        # shared in-memory action log
        self.actions: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_agents(self) -> None:
        """Import all modules in the ``agents`` sub-package and register
        every BaseAgent subclass found."""
        from pm_orchestrator import agents as agents_pkg

        for module_info in pkgutil.iter_modules(agents_pkg.__path__):
            module = importlib.import_module(f"pm_orchestrator.agents.{module_info.name}")
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, BaseAgent)
                    and obj is not BaseAgent
                    and obj.name
                    and obj.name not in self._runners
                ):
                    self._register(obj())
                    logger.info("Registered agent: %s", obj.name)

    def _register(self, agent: BaseAgent) -> None:
        rc = RuntimeConfig(
            auto_risk=["low"],
            confirm_risk=["medium", "high"],
        )
        self._runners[agent.name] = ReActRunner(agent=agent, runtime_config=rc)

    # ------------------------------------------------------------------
    # Agent info
    # ------------------------------------------------------------------

    def list_agents(self) -> list[dict[str, str]]:
        """Return name + description for every registered agent."""
        return [
            {"name": r.agent.name, "description": r.agent.description}
            for r in self._runners.values()
        ]

    def _runner(self, agent_name: str) -> ReActRunner:
        if agent_name not in self._runners:
            raise KeyError(f"Agent not found: {agent_name!r}")
        return self._runners[agent_name]

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    async def invoke(self, agent_name: str, message: str, session_id: str) -> AgentResult:
        result = await self._runner(agent_name).invoke(message, session_id)
        self._index_confirms(agent_name, result)
        self._log_actions(result)
        return result

    async def resume(self, confirm_id: str, approved: bool) -> AgentResult:
        agent_name = self._confirm_index.get(confirm_id)
        if agent_name is None:
            raise KeyError(f"Confirm not found: {confirm_id!r}")
        result = await self._runner(agent_name).resume(confirm_id, approved)
        self._confirm_index.pop(confirm_id, None)
        self._log_actions(result)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _index_confirms(self, agent_name: str, result: AgentResult) -> None:
        if result.pending_confirm:
            self._confirm_index[result.pending_confirm.confirm_id] = agent_name

    def _log_actions(self, result: AgentResult) -> None:
        loggable = {"tool_call", "tool_result", "confirm_wait", "confirm_rejected", "tool_error"}
        for step in result.steps:
            if step.get("kind") in loggable:
                self.actions.append({"session_id": result.session_id, **step})
        if len(self.actions) > 500:
            self.actions[:] = self.actions[-500:]
