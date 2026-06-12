"""Pydantic schemas for eval harness."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.eval.constants import (
    DEFAULT_AGENT_CONCURRENCY,
    DEFAULT_GENERATOR_MODEL,
    DEFAULT_JUDGE_CONCURRENCY,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_SAMPLES,
    DEFAULT_SCENARIO_CONCURRENCY,
    DEFAULT_USER_TEXT_CONCURRENCY,
    MAX_JUDGE_SAMPLES,
)


class EvalSuite(str, Enum):
    CREATE_TASK = "create_task"
    UPDATE_TASK = "update_task"
    MULTI_TASK = "multi_task"
    HIERARCHY = "hierarchy"
    DUPLICATE_SEARCH = "duplicate_search"
    NO_TASK = "no_task"


class EvalRunStatus(str, Enum):
    QUEUED = "queued"
    GENERATING_SCENARIOS = "generating_scenarios"
    GENERATING_USER_TEXTS = "generating_user_texts"
    RUNNING_AGENTS = "running_agents"
    JUDGING = "judging"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class EvalCaseStatus(str, Enum):
    QUEUED = "queued"
    SCENARIO_GENERATING = "scenario_generating"
    SCENARIO_GENERATED = "scenario_generated"
    USER_TEXT_GENERATING = "user_text_generating"
    USER_TEXT_GENERATED = "user_text_generated"
    AGENT_RUNNING = "agent_running"
    AGENT_COMPLETED = "agent_completed"
    JUDGING = "judging"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class EvalRunConfig(BaseModel):
    n_cases: int = Field(default=50, ge=1, le=500)
    suites: list[str] = Field(
        default_factory=lambda: [s.value for s in EvalSuite],
    )
    # Tier-aware defaults: flash-lite stages wide, pricey pro judge narrow.
    scenario_generation_concurrency: int = Field(default=DEFAULT_SCENARIO_CONCURRENCY, ge=1, le=100)
    user_text_generation_concurrency: int = Field(
        default=DEFAULT_USER_TEXT_CONCURRENCY, ge=1, le=100
    )
    agent_concurrency: int = Field(default=DEFAULT_AGENT_CONCURRENCY, ge=1, le=100)
    judge_concurrency: int = Field(default=DEFAULT_JUDGE_CONCURRENCY, ge=1, le=100)
    timeout_sec_per_case: int = Field(default=180, ge=30, le=600)
    generator_model: str = DEFAULT_GENERATOR_MODEL
    judge_model: str = DEFAULT_JUDGE_MODEL
    use_llm_judge: bool = True
    use_real_tracker: bool = False
    # Self-consistency panel: judge each case K times and aggregate (1 = off).
    judge_samples: int = Field(default=DEFAULT_JUDGE_SAMPLES, ge=1, le=MAX_JUDGE_SAMPLES)
    # Fake-tracker realism: simulate per-tool latency distributions and (opt-in)
    # transient 429/error behavior so latency numbers and timeouts mean something.
    simulate_tool_latency: bool = True
    simulate_tracker_errors: bool = False
    tool_latency_scale: float = Field(default=1.0, ge=0.0, le=10.0)
    extra: dict[str, Any] = Field(default_factory=dict)


class EvalOperation(BaseModel):
    operation: str
    payload: dict[str, Any] = Field(default_factory=dict)
    query: str | None = None
    task_key: str | None = None
    result_used: list[str] = Field(default_factory=list)


class NormalizedAgentOutput(BaseModel):
    operations: list[EvalOperation] = Field(default_factory=list)
    final_answer: str | None = None


class DeterministicEvaluation(BaseModel):
    passed: bool
    errors: list[dict[str, Any]] = Field(default_factory=list)


class JudgeCriterionScore(BaseModel):
    score: float = Field(ge=0, le=10)
    weight: float
    reason: str = ""


class LLMJudgeEvaluation(BaseModel):
    criteria: dict[str, JudgeCriterionScore] = Field(default_factory=dict)
    weighted_score: float = Field(default=0.0, ge=0, le=10)
    passed: bool = False
    score: float = Field(default=0.0, ge=0, le=1)
    explanation: str = ""
    judge_model: str | None = None
    technical_error: str | None = None
    # ── Self-consistency panel signals ──────────────────────────────────────
    # How many judge samples were aggregated, how much they agreed (0..1), and
    # the per-criterion spread. low_confidence flags verdicts worth a human look.
    samples: int = 1
    confidence: float = Field(default=1.0, ge=0, le=1)
    low_confidence: bool = False
    weighted_score_stddev: float | None = None
    criteria_stddev: dict[str, float] = Field(default_factory=dict)
    # Failure-mode tags the judge attached (e.g. "hallucinated_field"), used by
    # the run-level diagnosis to cluster "where the agent is dumb".
    failure_modes: list[str] = Field(default_factory=list)
    # Measured judge token usage (summed across panel samples) → real judge cost.
    judge_prompt_tokens: int = 0
    judge_completion_tokens: int = 0

    @property
    def semantic_pass(self) -> bool:
        """Deprecated alias for passed."""
        return self.passed


class FinalEvaluation(BaseModel):
    passed: bool
    score: float
    weighted_score: float = Field(default=0.0, ge=0, le=10)
    criteria: dict[str, JudgeCriterionScore] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class DiagnosisProblem(BaseModel):
    title: str
    severity: Literal["high", "medium", "low"] = "medium"
    evidence: str = ""
    failure_modes: list[str] = Field(default_factory=list)
    affected_suites: list[str] = Field(default_factory=list)


class DiagnosisImprovement(BaseModel):
    area: Literal["prompt", "tools", "model", "data", "other"] = "prompt"
    suggestion: str
    rationale: str = ""
    priority: Literal["P0", "P1", "P2"] = "P1"


class DiagnosisReport(BaseModel):
    """«Штурм» verdict on where the agent is dumb + how to fix it."""

    summary: str = ""
    top_problems: list[DiagnosisProblem] = Field(default_factory=list)
    improvements: list[DiagnosisImprovement] = Field(default_factory=list)
    generated_by: str | None = None


class SyntheticScenario(BaseModel):
    goal: str
    expected_behavior: str
    suite: str
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    initial_state: dict[str, Any] = Field(default_factory=dict)
    expected_operations: list[dict[str, Any]] = Field(default_factory=list)
    forbidden_operations: list[dict[str, Any]] = Field(default_factory=list)
    expected_final_state: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
