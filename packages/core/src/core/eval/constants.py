"""Eval harness constants."""

DEFAULT_JUDGE_MODEL = "google/gemini-3.1-pro-preview"
DEFAULT_GENERATOR_MODEL = "google/gemini-3.1-flash-lite"
MAX_RESUME_PER_AGENT_CALL = 10
MAX_CONCURRENCY_PER_STAGE = 100

JUDGE_WEIGHTS: dict[str, float] = {
    "action_correctness": 0.40,
    "intent_alignment": 0.25,
    "forbidden_compliance": 0.15,
    "completeness": 0.10,
    "final_state_quality": 0.10,
}

JUDGE_PASS_WEIGHTED_MIN = 7.0
JUDGE_PASS_ACTION_MIN = 6.0
JUDGE_CRITERION_WARN_MIN = 6.0
JUDGE_PARSE_MAX_ATTEMPTS = 2
