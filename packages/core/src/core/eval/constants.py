"""Eval harness constants — «Штурм» quality evaluator."""

DEFAULT_JUDGE_MODEL = "google/gemini-3.1-pro-preview"
DEFAULT_GENERATOR_MODEL = "google/gemini-3.1-flash-lite"
MAX_RESUME_PER_AGENT_CALL = 10
MAX_CONCURRENCY_PER_STAGE = 100

# Eval scenarios operate exclusively on the team's real queue so generated
# fixtures, the fake board and the agent's writes all share one namespace.
EVAL_QUEUE = "DARKHORSE"

# ── Tier-aware concurrency defaults ────────────────────────────────────────
# Stages are barrier-separated, so peak in-flight OpenRouter load ≈ the active
# stage's semaphore. flash-lite stages (cheap, high TPM) run wide; the judge
# stage uses the pricey lower-TPM pro model, so it runs narrow to avoid 429.
# See docs/agent_evaluation.md → "Конкурентность и лимиты OpenRouter".
DEFAULT_SCENARIO_CONCURRENCY = 16
DEFAULT_USER_TEXT_CONCURRENCY = 16
DEFAULT_AGENT_CONCURRENCY = 10
DEFAULT_JUDGE_CONCURRENCY = 6

# ── Judge rubric ────────────────────────────────────────────────────────────
# faithfulness is a first-class safety axis (no invented tasks/fields/owners) —
# the design doc weights it highest among diagnostics. action_correctness stays
# the primary "did it do the right kind of thing" signal.
JUDGE_WEIGHTS: dict[str, float] = {
    "action_correctness": 0.30,
    "faithfulness": 0.25,
    "intent_alignment": 0.20,
    "forbidden_compliance": 0.10,
    "completeness": 0.10,
    "final_state_quality": 0.05,
}

JUDGE_PASS_WEIGHTED_MIN = 7.0
JUDGE_PASS_ACTION_MIN = 6.0
# Hallucination guardrail: a confidently-wrong invented field fails the case
# regardless of how high the weighted score is.
JUDGE_PASS_FAITHFULNESS_MIN = 5.0
JUDGE_CRITERION_WARN_MIN = 6.0
JUDGE_PARSE_MAX_ATTEMPTS = 2

# ── Self-consistency panel ──────────────────────────────────────────────────
# The judge is sampled K times and per-criterion scores are aggregated (median)
# so a single unlucky generation can't swing a verdict; the spread across
# samples becomes a confidence signal you can actually trust.
DEFAULT_JUDGE_SAMPLES = 3
MAX_JUDGE_SAMPLES = 7
# temperature=0 is deterministic → useless for a panel; sample with a little
# heat for diversity, but stay low so scores stay anchored to the rubric.
JUDGE_SAMPLE_TEMPERATURE = 0.35
# Below this normalized agreement the verdict is flagged "low confidence".
JUDGE_LOW_CONFIDENCE_THRESHOLD = 0.6

# ── OpenRouter pricing (USD per 1M tokens, input/output) ────────────────────
# Used for an honest judge-cost figure in the report. Update when prices move;
# see https://openrouter.ai/<model>. Unknown models fall back to DEFAULT_PRICING.
MODEL_PRICING_USD_PER_1M: dict[str, tuple[float, float]] = {
    "google/gemini-3.1-pro-preview": (2.0, 12.0),
    "google/gemini-3.1-flash-lite": (0.25, 1.5),
    "google/gemini-3.1-flash-lite-preview": (0.25, 1.5),
}
DEFAULT_PRICING_USD_PER_1M = (1.0, 3.0)


def model_cost_usd(model: str | None, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate USD cost for a number of prompt/completion tokens on a model."""
    in_rate, out_rate = MODEL_PRICING_USD_PER_1M.get(model or "", DEFAULT_PRICING_USD_PER_1M)
    return prompt_tokens / 1_000_000 * in_rate + completion_tokens / 1_000_000 * out_rate
