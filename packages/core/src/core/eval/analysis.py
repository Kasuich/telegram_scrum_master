"""Run-level failure analysis + LLM diagnosis — "where the agent is dumb".

Two layers:
1. Deterministic aggregation (cheap, always runs): cluster judge failure-mode
   tags + deterministic error types across failed cases, find weak suites /
   difficulties / criteria, and surface low-confidence verdicts.
2. LLM diagnosis (one call per run): feed a compact sample of failures to a
   strong model and get back root-cause hypotheses and prioritized,
   actionable fixes (prompt / tools / model / data).

Everything is stored inside the run's existing ``metrics_summary_json`` column,
so no schema migration is needed.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any

from core.eval.constants import JUDGE_WEIGHTS
from core.eval.schemas import DiagnosisImprovement, DiagnosisProblem, DiagnosisReport
from core.llm import LLMClient, Message

logger = logging.getLogger(__name__)

# Map deterministic checker error types onto the judge's failure-mode vocabulary
# so both signals cluster together.
_DET_ERROR_TO_MODE = {
    "forbidden_operation_executed": "forbidden_operation",
    "missing_operation": "incomplete_steps",
    "operation_mismatch": "wrong_action_type",
    "create_count_mismatch": "over_creation",
}

_MODE_LABELS = {
    "wrong_action_type": "Неверный тип действия",
    "hallucinated_field": "Галлюцинация полей (выдумал данные)",
    "missed_search": "Не выполнил поиск перед действием",
    "over_creation": "Создал лишние задачи",
    "under_creation": "Создал меньше задач, чем нужно",
    "forbidden_operation": "Запрещённая операция",
    "ignored_existing_task": "Проигнорировал существующую задачу",
    "incomplete_steps": "Неполное выполнение шагов",
    "wrong_priority_mapping": "Неверный маппинг приоритета",
    "wrong_assignee": "Неверный исполнитель",
    "no_action_when_needed": "Бездействие там, где нужно действие",
    "acted_when_not_needed": "Действие там, где не нужно",
    "looping_or_stalled": "Зацикливание / застревание",
    "other": "Прочее",
}


def _failed(case_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        r
        for r in case_rows
        if r.get("status") == "completed" and not r.get("passed")
    ]


def aggregate_failure_modes(case_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Count failure-mode tags across failed cases (judge tags + deterministic)."""
    counts: Counter[str] = Counter()
    suites_by_mode: dict[str, Counter[str]] = {}
    for row in _failed(case_rows):
        modes: set[str] = set()
        judge = row.get("llm_judge_evaluation") or {}
        for mode in judge.get("failure_modes") or []:
            modes.add(str(mode))
        det = row.get("deterministic_evaluation") or {}
        for err in det.get("errors") or []:
            etype = err.get("type") if isinstance(err, dict) else None
            if etype in _DET_ERROR_TO_MODE:
                modes.add(_DET_ERROR_TO_MODE[etype])
        for mode in modes:
            counts[mode] += 1
            suites_by_mode.setdefault(mode, Counter())[str(row.get("suite", "?"))] += 1

    out: list[dict[str, Any]] = []
    for mode, count in counts.most_common():
        out.append(
            {
                "mode": mode,
                "label": _MODE_LABELS.get(mode, mode),
                "count": count,
                "suites": dict(suites_by_mode.get(mode, Counter())),
            }
        )
    return out


def weak_spots(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Flag the weakest suites and criteria from the aggregated metrics."""
    spots: list[dict[str, Any]] = []
    for suite, stats in (metrics.get("suite_stats") or {}).items():
        pr = stats.get("pass_rate")
        if pr is not None and stats.get("n") and pr < 0.7:
            spots.append(
                {
                    "kind": "suite",
                    "name": suite,
                    "pass_rate": round(pr, 3),
                    "n": stats.get("n"),
                    "severity": "high" if pr < 0.4 else "medium",
                }
            )
    for criterion, avg in (metrics.get("criteria_avg") or {}).items():
        if criterion in JUDGE_WEIGHTS and avg is not None and avg < 7.0:
            spots.append(
                {
                    "kind": "criterion",
                    "name": criterion,
                    "avg_score": round(avg, 2),
                    "severity": "high" if avg < 5.0 else "medium",
                }
            )
    spots.sort(key=lambda s: 0 if s.get("severity") == "high" else 1)
    return spots


def build_failure_analysis(
    case_rows: list[dict[str, Any]], metrics: dict[str, Any]
) -> dict[str, Any]:
    """Deterministic analysis bundle (no LLM)."""
    failed = _failed(case_rows)
    low_conf = [
        r
        for r in case_rows
        if (r.get("llm_judge_evaluation") or {}).get("low_confidence")
    ]
    heuristic_judged = sum(
        1
        for r in case_rows
        if "heuristic" in str((r.get("llm_judge_evaluation") or {}).get("judge_model") or "")
    )
    return {
        "failure_modes": aggregate_failure_modes(case_rows),
        "weak_spots": weak_spots(metrics),
        "failed_count": len(failed),
        "low_confidence_count": len(low_conf),
        "heuristic_judged_count": heuristic_judged,
    }


def _sample_failures(case_rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Compact, token-bounded view of failed cases for the diagnosis prompt."""
    samples: list[dict[str, Any]] = []
    for row in _failed(case_rows)[:limit]:
        judge = row.get("llm_judge_evaluation") or {}
        normalized = row.get("agent_normalized_output") or {}
        ops = [
            o.get("operation")
            for o in (normalized.get("operations") or [])
            if isinstance(o, dict)
        ]
        samples.append(
            {
                "suite": row.get("suite"),
                "difficulty": row.get("difficulty"),
                "user_text": (row.get("user_text") or "")[:300],
                "agent_operations": ops,
                "failure_modes": judge.get("failure_modes") or [],
                "judge_explanation": (judge.get("explanation") or "")[:400],
                "weakest_criteria": _weakest_criteria(judge),
            }
        )
    return samples


def _weakest_criteria(judge: dict[str, Any]) -> dict[str, float]:
    criteria = judge.get("criteria") or {}
    scored = {
        name: float(item.get("score"))
        for name, item in criteria.items()
        if isinstance(item, dict) and item.get("score") is not None
    }
    return {k: v for k, v in sorted(scored.items(), key=lambda kv: kv[1])[:3]}


_DIAGNOSIS_SYSTEM = """\
You are «Штурм», diagnosing a PM agent (Yandex Tracker) from a failed-case sample.
Find WHERE the agent is dumb and HOW to fix it. Be concrete and engineering-actionable — name the
behavior, the likely root cause, and a specific change (prompt wording, tool/schema, model tier, or
training data). Prioritize by impact. Reply in Russian.

Return STRICT JSON only:
{
  "summary": "2-3 sentences: the dominant failure pattern(s)",
  "top_problems": [
    {"title": "...", "severity": "high|medium|low", "evidence": "which suites/cases/modes",
     "failure_modes": ["..."], "affected_suites": ["..."]}
  ],
  "improvements": [
    {"area": "prompt|tools|model|data|other", "suggestion": "concrete change",
     "rationale": "why it helps", "priority": "P0|P1|P2"}
  ]
}
Be terse: ≤4 problems, ≤5 improvements, one-line fields. severity ∈ high|medium|low,
priority ∈ P0|P1|P2, area ∈ prompt|tools|model|data|other. Output compact JSON, no markdown."""

_SEVERITY = {"high", "medium", "low"}
_PRIORITY = {"P0", "P1", "P2"}
_AREA = {"prompt", "tools", "model", "data", "other"}


def _extract_json(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("```"))
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start >= 0 and end > start else text


def _repair_json(text: str) -> str:
    text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)
    return text


def _norm(value: Any, allowed: set[str], default: str) -> str:
    s = str(value or "").strip()
    return s if s in allowed else default


def _coerce_diagnosis(raw: dict[str, Any], model: str) -> DiagnosisReport:
    """Build a DiagnosisReport leniently — never crash on an off-enum value."""
    problems: list[DiagnosisProblem] = []
    for p in (raw.get("top_problems") or [])[:6]:
        if not isinstance(p, dict):
            continue
        problems.append(
            DiagnosisProblem(
                title=str(p.get("title", ""))[:200],
                severity=_norm(p.get("severity"), _SEVERITY, "medium"),  # type: ignore[arg-type]
                evidence=str(p.get("evidence", ""))[:300],
                failure_modes=[str(m)[:40] for m in (p.get("failure_modes") or []) if m][:6],
                affected_suites=[str(s)[:40] for s in (p.get("affected_suites") or []) if s][:6],
            )
        )
    improvements: list[DiagnosisImprovement] = []
    for imp in (raw.get("improvements") or [])[:8]:
        if not isinstance(imp, dict):
            continue
        suggestion = str(imp.get("suggestion", "")).strip()
        if not suggestion:
            continue
        improvements.append(
            DiagnosisImprovement(
                area=_norm(imp.get("area"), _AREA, "other"),  # type: ignore[arg-type]
                suggestion=suggestion[:400],
                rationale=str(imp.get("rationale", ""))[:400],
                priority=_norm(imp.get("priority"), _PRIORITY, "P1"),  # type: ignore[arg-type]
            )
        )
    return DiagnosisReport(
        summary=str(raw.get("summary", ""))[:600],
        top_problems=problems,
        improvements=improvements,
        generated_by=model,
    )


async def run_diagnosis(
    case_rows: list[dict[str, Any]],
    metrics: dict[str, Any],
    *,
    model: str,
    sample_size: int = 14,
) -> DiagnosisReport:
    """One LLM pass over a sample of failures → actionable diagnosis. Degrades to
    an empty report on any error (diagnosis must never break a run)."""
    failures = _sample_failures(case_rows, sample_size)
    if not failures:
        return DiagnosisReport(
            summary="Провалов не обнаружено — агент прошёл все завершённые кейсы.",
            generated_by=model,
        )
    payload = {
        "metrics": {
            "pass_rate": metrics.get("pass_rate"),
            "criteria_avg": metrics.get("criteria_avg"),
            "suite_stats": metrics.get("suite_stats"),
            "failure_modes": aggregate_failure_modes(case_rows)[:10],
        },
        "failed_cases": failures,
    }
    # Generous budget: gemini-3.1 reasoning models spend thinking tokens against
    # max_tokens, so a tight cap truncated the JSON before any content was emitted.
    client = LLMClient(model=model, provider="openrouter", temperature=0.2, max_tokens=6000)
    try:
        response = await client.complete(
            [
                Message(role="system", content=_DIAGNOSIS_SYSTEM),
                Message(role="user", content=json.dumps(payload, ensure_ascii=False)),
            ]
        )
        text = _extract_json(response.content or "{}")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            raw = json.loads(_repair_json(text))
        return _coerce_diagnosis(raw if isinstance(raw, dict) else {}, model)
    except Exception as exc:
        logger.warning("Diagnosis generation failed: %s", exc)
        modes = aggregate_failure_modes(case_rows)
        top = modes[0]["label"] if modes else "разные ошибки"
        return DiagnosisReport(
            summary=f"Автодиагностика недоступна (ошибка LLM). Доминирующий режим отказа: {top}.",
            generated_by=f"{model}+unavailable",
        )
