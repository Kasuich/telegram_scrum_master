"""Export eval reports."""

from __future__ import annotations

import json
from typing import Any


def _fmt_pct(value: Any) -> str:
    return f"{value:.1%}" if isinstance(value, (int, float)) else "—"


def report_to_markdown(run: dict[str, Any], metrics: dict[str, Any]) -> str:
    cost = metrics.get("judge_cost_usd")
    share = metrics.get("tool_time_share")
    share_str = _fmt_pct(share) if share is not None else "—"
    lines = [
        f"# Отчёт «Штурм»: {run.get('name', '')}",
        "",
        f"- Статус: {run.get('status')}",
        f"- Pass rate: {_fmt_pct(metrics.get('pass_rate', 0))}",
        f"- Avg weighted score: {metrics.get('avg_weighted_score')}/10",
        f"- Faithfulness (avg): {metrics.get('faithfulness_avg')}/10 · "
        f"галлюцинации: {_fmt_pct(metrics.get('hallucination_rate', 0))}",
        f"- Доверие судьи: confidence {metrics.get('avg_judge_confidence')} · "
        f"low-confidence {_fmt_pct(metrics.get('low_confidence_rate', 0))}",
        f"- Avg/P95 agent latency: {metrics.get('avg_agent_latency_sec')}с / "
        f"{metrics.get('p95_agent_latency_sec')}с · доля времени тулзов: {share_str}",
        f"- Кейсы: {metrics.get('completed_cases', 0)} завершено, "
        f"{metrics.get('passed_cases', 0)} прошло"
        + (f" · judge cost ≈ ${cost}" if cost is not None else ""),
        "",
        "## Оценки судьи по критериям",
    ]
    for criterion, avg in (metrics.get("criteria_avg") or {}).items():
        lines.append(f"- {criterion}: {avg}/10")

    diagnosis = metrics.get("diagnosis") or {}
    if diagnosis:
        lines.append("")
        lines.append("## Диагноз «где агент тупит»")
        if diagnosis.get("summary"):
            lines.append(diagnosis["summary"])
        for problem in diagnosis.get("top_problems") or []:
            sev = str(problem.get("severity", "")).upper()
            lines.append(
                f"- **[{sev}] {problem.get('title', '')}** — {problem.get('evidence', '')}"
            )
        if diagnosis.get("improvements"):
            lines.append("")
            lines.append("### Что поправить")
            for imp in diagnosis["improvements"]:
                lines.append(
                    f"- `{imp.get('priority', '')}` ({imp.get('area', '')}): "
                    f"{imp.get('suggestion', '')} — {imp.get('rationale', '')}"
                )

    analysis = metrics.get("analysis") or {}
    if analysis.get("failure_modes"):
        lines.append("")
        lines.append("## Режимы отказа")
        for fm in analysis["failure_modes"]:
            lines.append(f"- ({fm.get('count')}) {fm.get('label', fm.get('mode'))}")

    lines.append("")
    lines.append("## По suite")
    for suite, stats in (metrics.get("suite_stats") or {}).items():
        lines.append(
            f"- **{suite}**: pass_rate={_fmt_pct(stats.get('pass_rate', 0))} n={stats.get('n')}"
        )

    lines.append("")
    lines.append("## Top errors")
    for err, count in metrics.get("top_errors") or []:
        lines.append(f"- ({count}) {err}")
    return "\n".join(lines)


def failed_cases_export(cases: list[dict[str, Any]]) -> str:
    return json.dumps(cases, ensure_ascii=False, indent=2)
