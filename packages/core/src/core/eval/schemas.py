"""Pydantic schemas for eval harness."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.eval.constants import DEFAULT_GENERATOR_MODEL, DEFAULT_JUDGE_MODEL


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
    scenario_generation_concurrency: int = Field(default=20, ge=1, le=100)
    user_text_generation_concurrency: int = Field(default=20, ge=1, le=100)
    agent_concurrency: int = Field(default=20, ge=1, le=100)
    judge_concurrency: int = Field(default=20, ge=1, le=100)
    timeout_sec_per_case: int = Field(default=180, ge=30, le=600)
    generator_model: str = DEFAULT_GENERATOR_MODEL
    judge_model: str = DEFAULT_JUDGE_MODEL
    use_llm_judge: bool = True
    use_real_tracker: bool = False
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
