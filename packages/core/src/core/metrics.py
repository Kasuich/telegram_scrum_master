"""
Prometheus metrics for PM Agent Platform.

Defines all application-level counters, histograms, and gauges.
Import this module early to register metrics before any scrape.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

# ── LLM ──────────────────────────────────────────────────────────────────────

llm_requests_total = Counter(
    "pm_llm_requests_total",
    "Total LLM API requests",
    ["model", "status"],  # status: success | error
)

llm_latency_seconds = Histogram(
    "pm_llm_latency_seconds",
    "LLM request latency in seconds",
    ["model"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0],
)

llm_tokens_total = Counter(
    "pm_llm_tokens_total",
    "Total tokens consumed",
    ["model", "token_type"],  # token_type: prompt | completion
)

# ── Tools ─────────────────────────────────────────────────────────────────────

tool_executions_total = Counter(
    "pm_tool_executions_total",
    "Total tool executions",
    ["tool_name", "risk", "status"],  # status: success | error
)

tool_latency_seconds = Histogram(
    "pm_tool_latency_seconds",
    "Tool execution latency in seconds",
    ["tool_name"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0],
)

# ── External services ─────────────────────────────────────────────────────────

external_requests_total = Counter(
    "pm_external_requests_total",
    "Total requests to external services",
    ["service", "status_code"],
)

external_latency_seconds = Histogram(
    "pm_external_latency_seconds",
    "External service request latency in seconds",
    ["service"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# ── Database ──────────────────────────────────────────────────────────────────

db_pool_checked_out = Gauge(
    "pm_db_pool_checked_out",
    "Number of currently checked-out DB connections",
)

# ── Agent ─────────────────────────────────────────────────────────────────────

agent_traces_total = Counter(
    "pm_agent_traces_total",
    "Total agent trace completions",
    ["agent_name", "finish_reason"],  # finish_reason: completed | failed | cancelled
)

agent_confirms_pending = Gauge(
    "pm_agent_confirms_pending",
    "Number of agent actions awaiting user confirmation",
)

agent_stage_visits_total = Counter(
    "pm_agent_stage_visits_total",
    "Total agent stage entries",
    ["agent_name", "stage"],
)

agent_stage_outcomes_total = Counter(
    "pm_agent_stage_outcomes_total",
    "Total agent stage outcomes",
    ["agent_name", "stage", "outcome"],
)

agent_graph_edges_total = Counter(
    "pm_agent_graph_edges_total",
    "Total traversals of agent graph edges",
    ["agent_name", "source", "target"],
)

agent_tool_calls_total = Counter(
    "pm_agent_tool_calls_total",
    "Total agent tool-call lifecycle events",
    ["agent_name", "stage", "tool_name", "risk", "status"],
)

agent_tool_outputs_total = Counter(
    "pm_agent_tool_outputs_total",
    "Total agent tool outputs by bounded result kind",
    ["agent_name", "stage", "tool_name", "result_kind"],
)


# ── Decorator helpers ─────────────────────────────────────────────────────────


def track_tool(
    tool_name: str, risk: str = "medium"
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that records tool execution metrics."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        import asyncio

        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.monotonic()
                status = "success"
                try:
                    return await func(*args, **kwargs)
                except Exception:
                    status = "error"
                    raise
                finally:
                    elapsed = time.monotonic() - start
                    tool_executions_total.labels(
                        tool_name=tool_name, risk=risk, status=status
                    ).inc()
                    tool_latency_seconds.labels(tool_name=tool_name).observe(elapsed)

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            status = "success"
            try:
                return func(*args, **kwargs)
            except Exception:
                status = "error"
                raise
            finally:
                elapsed = time.monotonic() - start
                tool_executions_total.labels(tool_name=tool_name, risk=risk, status=status).inc()
                tool_latency_seconds.labels(tool_name=tool_name).observe(elapsed)

        return sync_wrapper

    return decorator


_KNOWN_STAGES = [
    "INTAKE",
    "STATUS",
    "BOARD",
    "TRANSITION",
    "QUERY",
    "REORG",
    "PROACTIVE",
    "HYGIENE",
    "DIALOG",
]
_KNOWN_TOOL_STATUSES = ["requested", "completed", "failed", "rejected", "guard_rejected"]


def init_agent_metrics(agent_name: str) -> None:
    """Pre-initialize counters for a given agent so Prometheus captures a 0
    baseline before the first event, enabling increase() to work correctly."""
    for stage in _KNOWN_STAGES:
        agent_stage_visits_total.labels(agent_name=agent_name, stage=stage)


__all__ = [
    "llm_requests_total",
    "llm_latency_seconds",
    "llm_tokens_total",
    "tool_executions_total",
    "tool_latency_seconds",
    "external_requests_total",
    "external_latency_seconds",
    "db_pool_checked_out",
    "agent_traces_total",
    "agent_confirms_pending",
    "agent_stage_visits_total",
    "agent_stage_outcomes_total",
    "agent_graph_edges_total",
    "agent_tool_calls_total",
    "agent_tool_outputs_total",
    "track_tool",
    "init_agent_metrics",
]
