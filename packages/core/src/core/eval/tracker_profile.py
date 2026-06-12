"""Latency + transient-error model so the fake Tracker behaves like the real one.

The in-memory fake Tracker otherwise answers instantly, which made eval latency
numbers meaningless (``agent_latency_sec`` was pure LLM time) and made timeouts
impossible to ever observe. Here we sample each tool's wall-time from a
log-normal fitted to realistic Yandex Tracker REST latencies (median + p95), so
``agent_latency_sec`` and per-step trace durations reflect real-world tool cost
and slow-tail behavior — and per-case timeouts can actually trigger.

The distribution is driven by a per-case seeded RNG (see ``FakeTrackerStore``),
so a given case replays identically across runs.

The defaults below are grounded estimates for a cloud SaaS REST API reached over
the internet with auth. To pin them to your environment, replace
``TRACKER_LATENCY_MS`` with values read from the production Prometheus
histograms ``external_latency_seconds{service="tracker"}`` /
``pm_tool_latency_seconds`` (median + p95 per operation).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# (median_ms, p95_ms) per logical Tracker operation.
TRACKER_LATENCY_MS: dict[str, tuple[float, float]] = {
    "get_issue": (160.0, 480.0),
    "search": (600.0, 1800.0),
    "create_issue": (450.0, 1300.0),
    "patch_issue": (380.0, 1100.0),
    "comment": (300.0, 850.0),
    "transition": (480.0, 1300.0),
    "default": (250.0, 700.0),
}

# z-score for the 95th percentile of a normal distribution.
_P95_Z = 1.6448536269514722


def _lognormal_params(median_ms: float, p95_ms: float) -> tuple[float, float]:
    """Fit a log-normal so that exp(mu)=median and the 95th pct matches p95."""
    mu = math.log(max(median_ms, 1.0))
    sigma = max((math.log(max(p95_ms, median_ms + 1.0)) - mu) / _P95_Z, 0.0)
    return mu, sigma


@dataclass
class ToolLatencyProfile:
    """Samples realistic per-tool latency (ms) and occasional rate-limit stalls."""

    scale: float = 1.0
    simulate_errors: bool = False
    # ~1 in 25 calls hits a transient slowdown when error simulation is on.
    ratelimit_prob: float = 0.04
    ratelimit_extra_ms: tuple[float, float] = (800.0, 4000.0)
    table: dict[str, tuple[float, float]] = field(
        default_factory=lambda: dict(TRACKER_LATENCY_MS)
    )

    def sample_ms(self, op: str, rng: random.Random) -> float:
        """Sample a wall-time in milliseconds for one tool call of kind ``op``."""
        median, p95 = self.table.get(op, self.table["default"])
        mu, sigma = _lognormal_params(median, p95)
        base = math.exp(rng.gauss(mu, sigma)) if sigma > 0 else median
        if self.simulate_errors and rng.random() < self.ratelimit_prob:
            lo, hi = self.ratelimit_extra_ms
            base += rng.uniform(lo, hi)
        return max(0.0, base * self.scale)


# REST routing (method, path) → logical op. Mirrors FakeTrackerStore.request().
def classify_request(method: str, path: str) -> str:
    method = method.upper()
    stripped = path.rstrip("/")
    if method == "GET" and "/issues/" in path and "/comments" not in path:
        return "get_issue"
    if method == "POST" and stripped.endswith("/issues/_search"):
        return "search"
    if method == "POST" and stripped.endswith("/issues"):
        return "create_issue"
    if method == "PATCH" and "/issues/" in path:
        return "patch_issue"
    if method == "POST" and "/transitions/" in path:
        return "transition"
    if method == "POST" and "/comments" in path:
        return "comment"
    if method == "GET" and "/comments" in path:
        return "get_issue"
    return "default"


# MCP tool name → logical op. Mirrors FakeTrackerStore.mcp_call().
_MCP_OP: dict[str, str] = {
    "GetIssue": "get_issue",
    "GetIssues": "search",
    "SearchEntities": "search",
    "CreateIssue": "create_issue",
    "UpdateIssue": "patch_issue",
    "CreateComment": "comment",
    "ChangeIssueStatus": "transition",
}


def classify_tool(tool_name: str) -> str:
    return _MCP_OP.get(tool_name.strip(), "default")
