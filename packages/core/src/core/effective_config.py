"""
Effective-config helpers for PM Agent Platform.

Implements the three-layer merge for agent runtime configuration:

    class defaults  <  agent_specs (DB)  <  agent_instances.overlay (DB)

Layer semantics:
- **Class**: hard-coded in the Python class (always present, code-only changes)
- **Spec**: shared across teams; edited by devs via console without deploy
- **Overlay**: per-team customisation; edited by PM/admin via console

``build_effective_config`` is a pure function that accepts pre-loaded dicts so
it can be tested without a database connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.agent import BaseAgent, LLMSettings
from core.config import RuntimeConfig


@dataclass
class EffectiveAgentConfig:
    """Merged runtime configuration for a single agent + team combination."""

    prompt: str
    llm_configs: list[LLMSettings]
    runtime_config: RuntimeConfig


def _merge_tool_overrides(
    spec_tools: Any, overlay_tools: Any
) -> tuple[list[str], dict[str, bool]]:
    """Collapse spec + overlay per-tool config into runtime fields.

    Each layer maps ``tool_name -> {"enabled": bool, "confirm": bool|null}``.
    Overlay entries win over spec entries.
    """
    merged: dict[str, dict[str, Any]] = {}
    for layer in (spec_tools, overlay_tools):
        if isinstance(layer, dict):
            for name, cfg in layer.items():
                if isinstance(cfg, dict):
                    merged.setdefault(name, {}).update(cfg)

    disabled = [name for name, cfg in merged.items() if cfg.get("enabled") is False]
    tool_confirm = {
        name: bool(cfg["confirm"])
        for name, cfg in merged.items()
        if cfg.get("confirm") is not None
    }
    return disabled, tool_confirm


def build_effective_config(
    agent: BaseAgent,
    spec: dict[str, Any] | None,
    overlay: dict[str, Any] | None,
) -> EffectiveAgentConfig:
    """Merge agent class defaults with DB-stored spec and overlay.

    Parameters
    ----------
    agent:
        The ``BaseAgent`` subclass instance (provides class-level defaults).
    spec:
        Dict from ``AgentSpec`` row: may contain ``prompt``, ``model``,
        ``autonomy`` (sub-dict with ``auto_risk`` / ``confirm_risk`` /
        ``always_confirm_tools``).  ``None`` means no spec row exists.
    overlay:
        Dict from ``AgentInstance.overlay``: same keys as spec, takes
        highest priority.  ``None`` means no overlay set.
    """
    spec = spec or {}
    overlay = overlay or {}

    # ── Prompt ───────────────────────────────────────────────────────────
    prompt = agent.prompt
    if spec.get("prompt"):
        prompt = spec["prompt"]
    if overlay.get("prompt"):
        prompt = overlay["prompt"]

    # ── LLM configs ──────────────────────────────────────────────────────
    llm_configs = list(agent.llm_configs) or [LLMSettings()]
    # Model override: spec first, then overlay wins (higher priority)
    model_override: str | None = spec.get("model") or None
    if overlay.get("model"):
        model_override = overlay["model"]
    if model_override:
        llm_configs = [
            LLMSettings(**{**cfg.model_dump(exclude_none=True), "model": model_override})
            for cfg in llm_configs
        ]

    # ── RuntimeConfig (autonomy thresholds) ──────────────────────────────
    # Start from class-level defaults.
    base_auto: list[str] = ["low"]
    base_confirm: list[str] = ["medium", "high"]
    base_always: list[str] = []

    spec_autonomy: dict[str, Any] = spec.get("autonomy") or {}
    overlay_autonomy: dict[str, Any] = overlay.get("autonomy") or {}

    def _pick(key: str, default: Any) -> Any:
        # overlay wins over spec wins over default
        if key in overlay_autonomy:
            return overlay_autonomy[key]
        if key in overlay:  # flat override in overlay root
            return overlay[key]
        if key in spec_autonomy:
            return spec_autonomy[key]
        return default

    auto_risk: list[str] = _pick("auto_risk", base_auto)
    confirm_risk: list[str] = _pick("confirm_risk", base_confirm)
    always_confirm: list[str] = _pick("always_confirm_tools", base_always)

    # ── Per-tool overrides ───────────────────────────────────────────────
    # overlay["tools"] = {tool_name: {"enabled": bool, "confirm": bool|null}}
    # overlay wins over spec.
    disabled_tools, tool_confirm = _merge_tool_overrides(
        spec.get("tools"), overlay.get("tools")
    )

    runtime_config = RuntimeConfig(
        auto_risk=auto_risk,
        confirm_risk=confirm_risk,
        always_confirm_tools=always_confirm,
        disabled_tools=disabled_tools,
        tool_confirm=tool_confirm,
    )

    return EffectiveAgentConfig(
        prompt=prompt,
        llm_configs=llm_configs,
        runtime_config=runtime_config,
    )


__all__ = ["EffectiveAgentConfig", "build_effective_config"]
