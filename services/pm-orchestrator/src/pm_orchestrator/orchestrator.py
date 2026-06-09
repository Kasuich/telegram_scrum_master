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
import json
import logging
import pkgutil
import uuid
from typing import Any

from core.agent import BaseAgent
from core.config import get_config
from core.effective_config import EffectiveAgentConfig, build_effective_config
from core.exceptions import AgentError
from core.invocation import InvocationContext, normalize_invocation_context
from core.metrics import (
    agent_confirms_pending,
    agent_graph_edges_total,
    agent_stage_outcomes_total,
    agent_stage_visits_total,
    agent_tool_calls_total,
    agent_tool_outputs_total,
)
from core.react import AgentResult, ReActRunner
from core.telemost_shortcut import try_meeting_capture_shortcut
from core.tools import get_registry

logger = logging.getLogger(__name__)


class OrchestratorService:
    """Central service that owns all agent runners."""

    def __init__(self) -> None:
        self._runners: dict[str, ReActRunner] = {}
        # global confirm_id → agent_name index (runners share memory stores)
        self._confirm_index: dict[str, str] = {}
        # shared in-memory action log
        self.actions: list[dict[str, Any]] = []
        # DB persistence (configured at startup via configure_persistence)
        self._db_enabled: bool = False
        self._team_id: str | None = None

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
        self._runners[agent.name] = ReActRunner(
            agent=agent,
            runtime_config=get_config().runtime,
        )

    # ------------------------------------------------------------------
    # Persistence setup
    # ------------------------------------------------------------------

    def configure_persistence(self) -> None:
        """Enable DB persistence when a database_url and default team are set.

        Safe to call at startup; degrades to in-memory when either is missing.
        """
        cfg = get_config()
        team_id = cfg.app.default_team_id
        if cfg.database_url and team_id:
            self._db_enabled = True
            self._team_id = team_id
            logger.info("DB persistence enabled (team_id=%s)", team_id)
        else:
            self._db_enabled = False
            logger.info("DB persistence disabled (in-memory mode)")

    async def ensure_schema_and_seed(self) -> None:
        """Create tables (idempotent) and seed the default team.

        On failure, logs a warning and falls back to in-memory mode so the
        service still starts.
        """
        if not self._db_enabled or self._team_id is None:
            return
        try:
            from core.daily_digest import ensure_daily_digest_scheduled_job
            from core.db import create_all_tables, get_session
            from core.seed import ensure_agent_instances, ensure_default_team

            await create_all_tables()
            async with get_session() as session:
                await ensure_default_team(session, self._team_id)
                await ensure_agent_instances(session, self._team_id, list(self._runners))
                await ensure_daily_digest_scheduled_job(session, self._team_id)
            logger.info("Schema ensured and default team seeded")
        except Exception as exc:
            logger.warning("DB init failed, falling back to in-memory: %s", exc)
            self._db_enabled = False

    # ------------------------------------------------------------------
    # Effective config
    # ------------------------------------------------------------------

    async def _load_effective_config(self, agent_name: str) -> EffectiveAgentConfig | None:
        """Load AgentSpec + AgentInstance overlay from DB and build effective config.

        Returns ``None`` when DB is disabled or records don't exist (callers
        fall back to class defaults in that case).
        """
        if not self._db_enabled or self._team_id is None:
            return None

        runner = self._runners.get(agent_name)
        if runner is None:
            return None

        try:
            from core.db import get_session
            from core.models import AgentInstance, AgentSpec
            from sqlalchemy import select

            async with get_session() as session:
                spec_row = (
                    await session.execute(select(AgentSpec).where(AgentSpec.name == agent_name))
                ).scalar_one_or_none()

                instance_row = (
                    await session.execute(
                        select(AgentInstance).where(
                            AgentInstance.team_id == __import__("uuid").UUID(self._team_id),
                            AgentInstance.name == agent_name,
                        )
                    )
                ).scalar_one_or_none()

            spec_data = (
                {
                    "prompt": spec_row.prompt,
                    "model": spec_row.model,
                    "autonomy": spec_row.autonomy,
                }
                if spec_row
                else None
            )
            overlay_data = instance_row.overlay if instance_row else None

            return build_effective_config(runner.agent, spec_data, overlay_data)

        except Exception as exc:
            logger.warning("Failed to load effective config for %s: %s", agent_name, exc)
            return None

    async def _ensure_agent_enabled(self, agent_name: str) -> None:
        """Block runtime calls when the console kill-switch disabled an agent."""
        if not self._db_enabled or self._team_id is None:
            return

        try:
            import uuid

            from core.db import get_session
            from core.models import AgentInstance
            from sqlalchemy import select

            async with get_session() as session:
                row = (
                    await session.execute(
                        select(AgentInstance).where(
                            AgentInstance.team_id == uuid.UUID(self._team_id),
                            AgentInstance.name == agent_name,
                        )
                    )
                ).scalar_one_or_none()
        except Exception as exc:
            logger.warning("Failed to check agent enabled state for %s: %s", agent_name, exc)
            return

        if row is not None and not row.enabled:
            raise AgentError(f"Agent disabled by console kill-switch: {agent_name}")

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

    async def invoke(
        self,
        agent_name: str,
        message: str,
        session_id: str,
        context: InvocationContext | dict[str, Any] | None = None,
    ) -> AgentResult:
        invocation_context = normalize_invocation_context(context)
        shortcut = await try_meeting_capture_shortcut(
            message, session_id, context=invocation_context
        )
        if shortcut is not None:
            return shortcut

        runner = self._runner(agent_name)
        await self._ensure_agent_enabled(agent_name)
        eff = await self._load_effective_config(agent_name)
        eff_prompt = eff.prompt if eff else None
        eff_rc = eff.runtime_config if eff else None

        if self._db_enabled:
            from core.db import get_session

            async with get_session() as session:
                result = await runner.invoke(
                    message,
                    session_id,
                    db_session=session,
                    team_id=self._team_id,
                    effective_prompt=eff_prompt,
                    effective_runtime_config=eff_rc,
                    invocation_context=invocation_context,
                )
        else:
            result = await runner.invoke(
                message,
                session_id,
                effective_prompt=eff_prompt,
                effective_runtime_config=eff_rc,
                invocation_context=invocation_context,
            )
        self._index_confirms(agent_name, result)
        self._log_actions(agent_name, result)
        return result

    async def resume(self, confirm_id: str, approved: bool) -> AgentResult:
        agent_name = self._confirm_index.get(confirm_id)
        if agent_name is None:
            agent_name = await self._lookup_agent_name_for_confirm_db(confirm_id)
        if agent_name is None:
            raise KeyError(f"Confirm not found: {confirm_id!r}")
        runner = self._runner(agent_name)
        await self._ensure_agent_enabled(agent_name)
        eff = await self._load_effective_config(agent_name)
        eff_prompt = eff.prompt if eff else None
        eff_rc = eff.runtime_config if eff else None

        if self._db_enabled:
            from core.db import get_session

            async with get_session() as session:
                result = await runner.resume(
                    confirm_id,
                    approved,
                    db_session=session,
                    team_id=self._team_id,
                    effective_prompt=eff_prompt,
                    effective_runtime_config=eff_rc,
                )
        else:
            result = await runner.resume(
                confirm_id,
                approved,
                effective_prompt=eff_prompt,
                effective_runtime_config=eff_rc,
            )
        self._confirm_index.pop(confirm_id, None)
        agent_confirms_pending.set(len(self._confirm_index))
        self._log_actions(agent_name, result)
        return result

    async def _lookup_agent_name_for_confirm_db(self, confirm_id: str) -> str | None:
        if not self._db_enabled:
            return None

        try:
            from core.db import get_session
            from core.models import Action, AgentInstance, Confirm, Trace
            from sqlalchemy import select

            async with get_session() as session:
                stmt = (
                    select(Action, AgentInstance, Trace)
                    .join(Confirm, Confirm.action_id == Action.id)
                    .outerjoin(AgentInstance, Action.agent_instance_id == AgentInstance.id)
                    .outerjoin(Trace, Action.trace_id == Trace.id)
                    .where(Confirm.id == uuid.UUID(confirm_id))
                )
                row = (await session.execute(stmt)).one_or_none()
        except Exception as exc:
            logger.warning("Failed to resolve confirm %s from DB: %s", confirm_id, exc)
            return None

        if row is None:
            return None

        action, agent_instance, trace = row
        del action
        if agent_instance is not None and agent_instance.name:
            return agent_instance.name

        meta = (trace.metadata_json or {}) if trace is not None else {}
        agent_name = meta.get("agent_name")
        if isinstance(agent_name, str) and agent_name:
            return agent_name
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _index_confirms(self, agent_name: str, result: AgentResult) -> None:
        if result.pending_confirm:
            self._confirm_index[result.pending_confirm.confirm_id] = agent_name
        agent_confirms_pending.set(len(self._confirm_index))

    @staticmethod
    def _result_kind(result: Any) -> str:
        if result is None:
            return "none"
        if isinstance(result, dict):
            if result.get("error"):
                return "error"
            return "empty_object" if not result else "object"
        if isinstance(result, list):
            return "empty_list" if not result else "list"
        if isinstance(result, bool):
            return "boolean"
        if isinstance(result, (int, float)):
            return "number"
        if isinstance(result, str):
            return "empty_string" if not result else "string"
        return "other"

    @staticmethod
    def _event_json(event: dict[str, Any]) -> str:
        payload = json.dumps(event, ensure_ascii=False, default=str, separators=(",", ":"))
        if len(payload) <= 16_000:
            return payload
        return json.dumps(
            {
                "event": "agent_step",
                "agent_name": event.get("agent_name"),
                "session_id": event.get("session_id"),
                "stage": event.get("stage"),
                "kind": event.get("kind"),
                "tool_name": event.get("tool_name"),
                "truncated": True,
                "preview": payload[:12_000],
            },
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )

    def _log_actions(self, agent_name: str, result: AgentResult) -> None:
        loggable = {
            "stage",
            "tool_call",
            "tool_result",
            "confirm_wait",
            "confirm_rejected",
            "tool_error",
            "clarification",
            "final",
        }
        stage = "unknown"
        tool_risks = {tool.name: tool.risk for tool in get_registry().list()}
        for step in result.steps:
            kind = str(step.get("kind", "unknown"))
            if kind == "stage":
                stage = str(step.get("stage") or "unknown")
                agent_stage_visits_total.labels(agent_name=agent_name, stage=stage).inc()
            elif kind == "tool_call":
                tool_name = str(step.get("tool_name") or "unknown")
                risk = tool_risks.get(tool_name, "unknown")
                agent_graph_edges_total.labels(
                    agent_name=agent_name, source=stage, target=tool_name
                ).inc()
                agent_tool_calls_total.labels(
                    agent_name=agent_name,
                    stage=stage,
                    tool_name=tool_name,
                    risk=risk,
                    status="requested",
                ).inc()
            elif kind == "confirm_wait":
                tool_name = str(step.get("tool_name") or "unknown")
                agent_tool_calls_total.labels(
                    agent_name=agent_name,
                    stage=stage,
                    tool_name=tool_name,
                    risk=tool_risks.get(tool_name, "unknown"),
                    status="pending",
                ).inc()
                agent_graph_edges_total.labels(
                    agent_name=agent_name, source=tool_name, target="pending"
                ).inc()
            elif kind == "tool_result":
                tool_name = str(step.get("tool_name") or "unknown")
                result_kind = self._result_kind(step.get("result"))
                agent_tool_calls_total.labels(
                    agent_name=agent_name,
                    stage=stage,
                    tool_name=tool_name,
                    risk=tool_risks.get(tool_name, "unknown"),
                    status="completed",
                ).inc()
                agent_tool_outputs_total.labels(
                    agent_name=agent_name,
                    stage=stage,
                    tool_name=tool_name,
                    result_kind=result_kind,
                ).inc()
                agent_graph_edges_total.labels(
                    agent_name=agent_name, source=tool_name, target=f"completed:{result_kind}"
                ).inc()
            elif kind in {"tool_error", "confirm_rejected"}:
                tool_name = str(step.get("tool_name") or "unknown")
                status = str(
                    step.get("status") or ("rejected" if kind == "confirm_rejected" else "failed")
                )
                agent_tool_calls_total.labels(
                    agent_name=agent_name,
                    stage=stage,
                    tool_name=tool_name,
                    risk=tool_risks.get(tool_name, "unknown"),
                    status=status,
                ).inc()
                agent_graph_edges_total.labels(
                    agent_name=agent_name, source=tool_name, target=status
                ).inc()
            elif kind in {"final", "clarification"}:
                outcome = (
                    "clarification"
                    if kind == "clarification"
                    else str(step.get("reason") or "completed")
                )
                agent_stage_outcomes_total.labels(
                    agent_name=agent_name, stage=stage, outcome=outcome
                ).inc()

            if step.get("kind") in loggable:
                event = {
                    "event": "agent_step",
                    "agent_name": agent_name,
                    "session_id": result.session_id,
                    "stage": stage,
                    **step,
                }
                self.actions.append(event)
                logger.info(
                    "agent_event",
                    extra={"agent_event": json.loads(self._event_json(event))},
                )
        if len(self.actions) > 500:
            self.actions[:] = self.actions[-500:]
