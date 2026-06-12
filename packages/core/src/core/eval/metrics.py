"""Aggregate eval metrics."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from core.eval.constants import JUDGE_PASS_FAITHFULNESS_MIN, JUDGE_WEIGHTS, model_cost_usd


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def compute_run_metrics(
    case_rows: list[dict[str, Any]], *, judge_model: str | None = None
) -> dict[str, Any]:
    """case_rows: dicts with suite, passed, score, latency_sec, agent_latency_sec, status."""
    completed = [r for r in case_rows if r.get("status") == "completed"]
    passed = [r for r in completed if r.get("passed")]
    timeouts = [r for r in case_rows if r.get("status") == "timeout"]

    latencies = [float(r["latency_sec"]) for r in completed if r.get("latency_sec") is not None]
    agent_latencies = [
        float(r["agent_latency_sec"]) for r in completed if r.get("agent_latency_sec") is not None
    ]

    by_suite: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in completed:
        by_suite[str(row.get("suite", "unknown"))].append(row)

    weighted_scores: list[float] = []
    criteria_sums: dict[str, float] = defaultdict(float)
    criteria_counts: dict[str, int] = defaultdict(int)
    criteria_by_suite: dict[str, Any] = {}

    for row in completed:
        judge = row.get("llm_judge_evaluation") or {}
        ws = judge.get("weighted_score")
        if ws is not None:
            weighted_scores.append(float(ws))
        for name in JUDGE_WEIGHTS:
            crit = (judge.get("criteria") or {}).get(name) or {}
            if "score" in crit:
                criteria_sums[name] += float(crit["score"])
                criteria_counts[name] += 1

    suite_stats: dict[str, Any] = {}
    agent_latency_by_suite: dict[str, Any] = {}
    for suite, rows in by_suite.items():
        suite_passed = sum(1 for r in rows if r.get("passed"))
        suite_agent = [
            float(r["agent_latency_sec"]) for r in rows if r.get("agent_latency_sec") is not None
        ]
        suite_stats[suite] = {
            "n": len(rows),
            "passed": suite_passed,
            "pass_rate": suite_passed / len(rows) if rows else 0.0,
        }
        agent_latency_by_suite[suite] = {
            "n": len(suite_agent),
            "avg": sum(suite_agent) / len(suite_agent) if suite_agent else None,
            "p95": percentile(suite_agent, 0.95),
        }
        suite_weighted: list[float] = []
        suite_action: list[float] = []
        for r in rows:
            judge = r.get("llm_judge_evaluation") or {}
            if judge.get("weighted_score") is not None:
                suite_weighted.append(float(judge["weighted_score"]))
            action = (judge.get("criteria") or {}).get("action_correctness") or {}
            if "score" in action:
                suite_action.append(float(action["score"]))
        criteria_by_suite[suite] = {
            "n": len(rows),
            "weighted_score": sum(suite_weighted) / len(suite_weighted) if suite_weighted else None,
            "action_correctness": sum(suite_action) / len(suite_action) if suite_action else None,
        }

    criteria_avg = {
        name: round(criteria_sums[name] / criteria_counts[name], 2)
        for name in JUDGE_WEIGHTS
        if criteria_counts[name] > 0
    }
    avg_weighted = (
        round(sum(weighted_scores) / len(weighted_scores), 2) if weighted_scores else None
    )

    errors: Counter[str] = Counter()
    for row in completed:
        if not row.get("passed"):
            ev = row.get("final_evaluation") or {}
            for err in ev.get("errors") or []:
                errors[str(err)[:120]] += 1
            det = row.get("deterministic_evaluation") or {}
            for err in det.get("errors") or []:
                errors[str(err.get("type", err))[:120]] += 1

    # ── Faithfulness / hallucination, judge confidence, trust split ─────────
    faith_scores: list[float] = []
    confidences: list[float] = []
    low_conf = 0
    hallucinated = 0
    llm_judged = 0
    heuristic_judged = 0
    judge_prompt_tokens = 0
    judge_completion_tokens = 0
    for row in completed:
        judge = row.get("llm_judge_evaluation") or {}
        crit = (judge.get("criteria") or {}).get("faithfulness") or {}
        if "score" in crit:
            fs = float(crit["score"])
            faith_scores.append(fs)
            if fs < JUDGE_PASS_FAITHFULNESS_MIN:
                hallucinated += 1
        if judge.get("confidence") is not None:
            confidences.append(float(judge["confidence"]))
        if judge.get("low_confidence"):
            low_conf += 1
        model_name = str(judge.get("judge_model") or "")
        if "heuristic" in model_name:
            heuristic_judged += 1
        elif model_name:
            llm_judged += 1
        judge_prompt_tokens += int(judge.get("judge_prompt_tokens") or 0)
        judge_completion_tokens += int(judge.get("judge_completion_tokens") or 0)

    # ── Per-tool simulated latency (fake tracker realism) ───────────────────
    tool_calls: dict[str, int] = defaultdict(int)
    tool_total: dict[str, float] = defaultdict(float)
    for row in completed:
        by_op = (row.get("tool_latency") or {}).get("by_op") or {}
        for op, stats in by_op.items():
            tool_calls[op] += int(stats.get("count") or 0)
            tool_total[op] += float(stats.get("total_sec") or 0.0)
    tool_latency_by_op = {
        op: {
            "calls": tool_calls[op],
            "total_sec": round(tool_total[op], 2),
            "avg_sec": round(tool_total[op] / tool_calls[op], 3) if tool_calls[op] else None,
        }
        for op in sorted(tool_calls, key=lambda o: tool_total[o], reverse=True)
    }
    total_tool_sec = round(sum(tool_total.values()), 2)
    total_agent_sec = sum(agent_latencies)
    tool_time_share = round(total_tool_sec / total_agent_sec, 3) if total_agent_sec else None

    judge_cost = (
        round(model_cost_usd(judge_model, judge_prompt_tokens, judge_completion_tokens), 4)
        if judge_model and (judge_prompt_tokens or judge_completion_tokens)
        else None
    )

    n_completed = len(completed)
    return {
        "pass_rate": len(passed) / n_completed if n_completed else 0.0,
        "faithfulness_avg": round(sum(faith_scores) / len(faith_scores), 2)
        if faith_scores
        else None,
        "hallucination_rate": hallucinated / n_completed if n_completed else 0.0,
        "avg_judge_confidence": round(sum(confidences) / len(confidences), 3)
        if confidences
        else None,
        "low_confidence_rate": low_conf / n_completed if n_completed else 0.0,
        "judge_trust": {"llm_judged": llm_judged, "heuristic_judged": heuristic_judged},
        "tool_latency_by_op": tool_latency_by_op,
        "total_tool_latency_sec": total_tool_sec,
        "tool_time_share": tool_time_share,
        "judge_tokens": {
            "prompt": judge_prompt_tokens,
            "completion": judge_completion_tokens,
        },
        "judge_cost_usd": judge_cost,
        "avg_latency_sec": sum(latencies) / len(latencies) if latencies else None,
        "p95_latency_sec": percentile(latencies, 0.95),
        "avg_agent_latency_sec": sum(agent_latencies) / len(agent_latencies)
        if agent_latencies
        else None,
        "p95_agent_latency_sec": percentile(agent_latencies, 0.95),
        "avg_weighted_score": avg_weighted,
        "avg_score": round(avg_weighted / 10.0, 4) if avg_weighted is not None else None,
        "criteria_avg": criteria_avg,
        "criteria_by_suite": criteria_by_suite,
        "timeout_rate": len(timeouts) / len(case_rows) if case_rows else 0.0,
        "suite_stats": suite_stats,
        "agent_latency_by_suite": agent_latency_by_suite,
        "top_errors": errors.most_common(10),
        "completed_cases": n_completed,
        "passed_cases": len(passed),
        "failed_cases": n_completed - len(passed),
        "timeout_cases": len(timeouts),
    }
