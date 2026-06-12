"""Export eval reports."""

from __future__ import annotations

import json
from typing import Any


def report_to_markdown(run: dict[str, Any], metrics: dict[str, Any]) -> str:
    lines = [
        f"# Eval Report: {run.get('name', '')}",
        "",
        f"- Status: {run.get('status')}",
        f"- Pass rate: {metrics.get('pass_rate', 0):.1%}",
        f"- Avg agent latency: {metrics.get('avg_agent_latency_sec')}",
        f"- P95 agent latency: {metrics.get('p95_agent_latency_sec')}",
        f"- Cases: {metrics.get('completed_cases', 0)} completed, "
        f"{metrics.get('passed_cases', 0)} passed",
        "",
        "## Judge scores",
        f"- Avg weighted score: {metrics.get('avg_weighted_score')}/10",
    ]
    for criterion, avg in (metrics.get("criteria_avg") or {}).items():
        lines.append(f"- {criterion}: {avg}/10")
    lines.append("")
    lines.append("## By suite")
    for suite, stats in (metrics.get("suite_stats") or {}).items():
        lines.append(f"- **{suite}**: pass_rate={stats.get('pass_rate', 0):.1%} n={stats.get('n')}")
    lines.append("")
    lines.append("### Criteria by suite")
    for suite, stats in (metrics.get("criteria_by_suite") or {}).items():
        ws = stats.get("weighted_score")
        ac = stats.get("action_correctness")
        lines.append(f"- **{suite}**: weighted={ws}/10 action={ac}/10 n={stats.get('n')}")
    lines.append("")
    lines.append("## Top errors")
    for err, count in metrics.get("top_errors") or []:
        lines.append(f"- ({count}) {err}")
    return "\n".join(lines)


def failed_cases_export(cases: list[dict[str, Any]]) -> str:
    return json.dumps(cases, ensure_ascii=False, indent=2)
