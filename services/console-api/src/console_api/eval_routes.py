"""Eval harness REST API routes."""

from __future__ import annotations

import subprocess
import uuid
from datetime import datetime
from typing import Any, Literal

from core.config import get_config
from core.db import get_session
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
from core.eval.export import failed_cases_export, report_to_markdown
from core.eval.repository import EvalRepository
from core.eval.schemas import EvalRunConfig
from core.models import EvalEvent, EvalRun, User
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from console_api.main import require_roles

router = APIRouter(prefix="/eval-runs", tags=["eval"])


class CreateEvalRunRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    n_cases: int = Field(default=50, ge=1, le=500)
    suites: list[str] = Field(default_factory=lambda: EvalRunConfig().suites)
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
    judge_samples: int = Field(default=DEFAULT_JUDGE_SAMPLES, ge=1, le=MAX_JUDGE_SAMPLES)
    simulate_tool_latency: bool = True
    simulate_tracker_errors: bool = False
    tool_latency_scale: float = Field(default=1.0, ge=0.0, le=10.0)


class EvalRunSummaryDTO(BaseModel):
    id: str
    name: str
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    total_cases: int
    completed_cases: int
    passed_cases: int
    failed_cases: int
    timeout_cases: int
    pass_rate: float | None
    avg_latency_sec: float | None
    p95_latency_sec: float | None
    avg_agent_latency_sec: float | None
    p95_agent_latency_sec: float | None


class EvalCaseRowDTO(BaseModel):
    id: str
    suite: str
    difficulty: str
    status: str
    passed: bool | None
    score: float | None
    weighted_score: float | None = None
    action_correctness: float | None = None
    faithfulness: float | None = None
    criteria_summary: dict[str, float] | None = None
    confidence: float | None = None
    low_confidence: bool | None = None
    failure_modes: list[str] = []
    agent_latency_sec: float | None
    latency_sec: float | None
    main_error: str | None
    user_text: str | None
    started_at: datetime | None
    finished_at: datetime | None


def _judge_fields(judge_json: dict[str, Any] | None) -> dict[str, Any]:
    if not judge_json:
        return {
            "weighted_score": None,
            "action_correctness": None,
            "faithfulness": None,
            "criteria_summary": None,
            "criteria": None,
            "judge_explanation": None,
            "confidence": None,
            "low_confidence": None,
            "failure_modes": [],
            "samples": None,
        }
    criteria_raw = judge_json.get("criteria") or {}
    criteria_summary = {
        name: float(item.get("score", 0))
        for name, item in criteria_raw.items()
        if isinstance(item, dict) and "score" in item
    }
    action = criteria_raw.get("action_correctness") or {}
    faithfulness = criteria_raw.get("faithfulness") or {}
    return {
        "weighted_score": judge_json.get("weighted_score"),
        "action_correctness": action.get("score") if isinstance(action, dict) else None,
        "faithfulness": faithfulness.get("score") if isinstance(faithfulness, dict) else None,
        "criteria_summary": criteria_summary or None,
        "criteria": criteria_raw or None,
        "judge_explanation": judge_json.get("explanation"),
        "confidence": judge_json.get("confidence"),
        "low_confidence": judge_json.get("low_confidence"),
        "failure_modes": judge_json.get("failure_modes") or [],
        "samples": judge_json.get("samples"),
    }


def _git_commit() -> str | None:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=2
            )
            .decode()
            .strip()
        )
    except Exception:
        return None


def _run_dto(run: EvalRun) -> EvalRunSummaryDTO:
    return EvalRunSummaryDTO(
        id=str(run.id),
        name=run.name,
        status=run.status,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        total_cases=run.total_cases,
        completed_cases=run.completed_cases,
        passed_cases=run.passed_cases,
        failed_cases=run.failed_cases,
        timeout_cases=run.timeout_cases,
        pass_rate=run.pass_rate,
        avg_latency_sec=run.avg_latency_sec,
        p95_latency_sec=run.p95_latency_sec,
        avg_agent_latency_sec=run.avg_agent_latency_sec,
        p95_agent_latency_sec=run.p95_agent_latency_sec,
    )


@router.post("")
async def create_eval_run(
    body: CreateEvalRunRequest,
    user: User = Depends(require_roles("dev", "admin")),
) -> dict[str, str]:
    if body.use_real_tracker and not get_config().allow_real_tracker_eval:
        raise HTTPException(
            status_code=403,
            detail="Real Tracker eval requires ALLOW_REAL_TRACKER_EVAL=true",
        )
    config = EvalRunConfig(
        n_cases=body.n_cases,
        suites=body.suites,
        scenario_generation_concurrency=body.scenario_generation_concurrency,
        user_text_generation_concurrency=body.user_text_generation_concurrency,
        agent_concurrency=body.agent_concurrency,
        judge_concurrency=body.judge_concurrency,
        timeout_sec_per_case=body.timeout_sec_per_case,
        generator_model=body.generator_model,
        judge_model=body.judge_model,
        use_llm_judge=body.use_llm_judge,
        use_real_tracker=body.use_real_tracker,
        judge_samples=body.judge_samples,
        simulate_tool_latency=body.simulate_tool_latency,
        simulate_tracker_errors=body.simulate_tracker_errors,
        tool_latency_scale=body.tool_latency_scale,
    )
    async with get_session() as session:
        repo = EvalRepository(session)
        run = await repo.create_run(
            name=body.name,
            config=config.model_dump(),
            created_by=user.email,
            generator_model=body.generator_model,
            judge_model=body.judge_model,
            git_commit=_git_commit(),
            total_cases=body.n_cases,
        )
        await session.commit()
        return {"run_id": str(run.id), "status": run.status}


@router.get("")
async def list_eval_runs(
    user: User = Depends(require_roles("dev", "admin")),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    del user
    async with get_session() as session:
        stmt = select(EvalRun).order_by(desc(EvalRun.created_at)).offset(offset).limit(limit)
        runs = (await session.execute(stmt)).scalars().all()
        total = len(runs)
        return {"items": [_run_dto(r).model_dump() for r in runs], "total": total}


@router.get("/{run_id}")
async def get_eval_run(
    run_id: uuid.UUID,
    user: User = Depends(require_roles("dev", "admin")),
) -> dict[str, Any]:
    del user
    async with get_session() as session:
        run = await session.get(EvalRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        dto = _run_dto(run).model_dump()
        dto["config"] = run.config_json
        dto["metrics_summary"] = run.metrics_summary_json
        dto["error_summary"] = run.error_summary_json
        dto["generator_model"] = run.generator_model
        dto["judge_model"] = run.judge_model
        dto["git_commit"] = run.git_commit
        return dto


@router.post("/{run_id}/cancel")
async def cancel_eval_run(
    run_id: uuid.UUID,
    user: User = Depends(require_roles("dev", "admin")),
) -> dict[str, str]:
    del user
    async with get_session() as session:
        repo = EvalRepository(session)
        ok = await repo.request_cancel(run_id)
        if not ok:
            raise HTTPException(status_code=400, detail="Cannot cancel run")
        await session.commit()
        return {"run_id": str(run_id), "status": "cancelling"}


@router.get("/{run_id}/cases")
async def list_eval_cases(
    run_id: uuid.UUID,
    user: User = Depends(require_roles("dev", "admin")),
    suite: str | None = None,
    status: str | None = None,
    passed: bool | None = None,
    search: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    del user
    async with get_session() as session:
        repo = EvalRepository(session)
        cases, total = await repo.list_cases(
            run_id,
            suite=suite,
            status=status,
            passed=passed,
            search=search,
            offset=offset,
            limit=limit,
        )
        items = []
        for case in cases:
            result = case.result
            main_error = None
            if result and result.final_evaluation_json:
                errs = result.final_evaluation_json.get("errors") or []
                main_error = errs[0] if errs else result.technical_error
            judge_fields = _judge_fields(result.llm_judge_evaluation_json if result else None)
            items.append(
                EvalCaseRowDTO(
                    id=str(case.id),
                    suite=case.suite,
                    difficulty=case.difficulty,
                    status=case.status,
                    passed=result.passed if result else None,
                    score=result.score if result else None,
                    weighted_score=judge_fields["weighted_score"],
                    action_correctness=judge_fields["action_correctness"],
                    faithfulness=judge_fields["faithfulness"],
                    criteria_summary=judge_fields["criteria_summary"],
                    confidence=judge_fields["confidence"],
                    low_confidence=judge_fields["low_confidence"],
                    failure_modes=judge_fields["failure_modes"],
                    agent_latency_sec=result.agent_latency_sec if result else None,
                    latency_sec=result.latency_sec if result else None,
                    main_error=main_error,
                    user_text=case.user_text,
                    started_at=case.started_at,
                    finished_at=case.finished_at,
                ).model_dump()
            )
        return {"items": items, "total": total}


@router.get("/{run_id}/cases/{case_id}")
async def get_eval_case(
    run_id: uuid.UUID,
    case_id: uuid.UUID,
    user: User = Depends(require_roles("dev", "admin")),
) -> dict[str, Any]:
    del user
    async with get_session() as session:
        repo = EvalRepository(session)
        case = await repo.get_case_detail(run_id, case_id)
        if not case:
            raise HTTPException(status_code=404, detail="Case not found")
        result = case.result
        judge_fields = _judge_fields(result.llm_judge_evaluation_json if result else None)
        tool_latency = None
        raw_output = result.agent_raw_output_json if result else None
        if isinstance(raw_output, dict):
            artifacts = raw_output.get("eval_artifacts") or {}
            if isinstance(artifacts, dict):
                tool_latency = artifacts.get("tool_latency")
        return {
            "case_id": str(case.id),
            "run_id": str(case.run_id),
            "suite": case.suite,
            "difficulty": case.difficulty,
            "status": case.status,
            "current_date": case.current_date,
            "generated_scenario": case.generated_scenario_json,
            "user_text": case.user_text,
            "initial_state": case.initial_state_json,
            "expected_operations": case.expected_operations_json,
            "forbidden_operations": case.forbidden_operations_json,
            "expected_final_state": case.expected_final_state_json,
            "agent_raw_output": result.agent_raw_output_json if result else None,
            "agent_normalized_output": result.agent_normalized_output_json if result else None,
            "final_fake_tracker_state": result.final_fake_tracker_state_json if result else None,
            "deterministic_evaluation": result.deterministic_evaluation_json if result else None,
            "llm_judge_evaluation": result.llm_judge_evaluation_json if result else None,
            "final_evaluation": result.final_evaluation_json if result else None,
            "weighted_score": judge_fields["weighted_score"],
            "criteria": judge_fields["criteria"],
            "criteria_summary": judge_fields["criteria_summary"],
            "action_correctness": judge_fields["action_correctness"],
            "faithfulness": judge_fields["faithfulness"],
            "judge_explanation": judge_fields["judge_explanation"],
            "confidence": judge_fields["confidence"],
            "low_confidence": judge_fields["low_confidence"],
            "failure_modes": judge_fields["failure_modes"],
            "samples": judge_fields["samples"],
            "tool_latency": tool_latency,
            "latency_sec": result.latency_sec if result else None,
            "agent_latency_sec": result.agent_latency_sec if result else None,
            "judge_latency_sec": result.judge_latency_sec if result else None,
            "agent_started_at": result.agent_started_at.isoformat()
            if result and result.agent_started_at
            else None,
            "agent_finished_at": result.agent_finished_at.isoformat()
            if result and result.agent_finished_at
            else None,
            "retry_count": result.retry_count if result else 0,
            "technical_error": result.technical_error if result else None,
            "passed": result.passed if result else None,
            "score": result.score if result else None,
        }


@router.get("/{run_id}/report")
async def get_eval_report(
    run_id: uuid.UUID,
    user: User = Depends(require_roles("dev", "admin")),
) -> dict[str, Any]:
    del user
    async with get_session() as session:
        run = await session.get(EvalRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return {
            "run": _run_dto(run).model_dump(),
            "metrics": run.metrics_summary_json or {},
            "errors": run.error_summary_json or {},
        }


@router.get("/{run_id}/events")
async def list_eval_events(
    run_id: uuid.UUID,
    user: User = Depends(require_roles("dev", "admin")),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    del user
    async with get_session() as session:
        stmt = (
            select(EvalEvent)
            .where(EvalEvent.run_id == run_id)
            .order_by(desc(EvalEvent.created_at))
            .limit(limit)
        )
        events = (await session.execute(stmt)).scalars().all()
        return [
            {
                "id": str(e.id),
                "case_id": str(e.case_id) if e.case_id else None,
                "level": e.level,
                "event_type": e.event_type,
                "message": e.message,
                "payload": e.payload_json,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ]


@router.get("/{run_id}/failed-cases")
async def export_failed_cases(
    run_id: uuid.UUID,
    user: User = Depends(require_roles("dev", "admin")),
) -> dict[str, Any]:
    del user
    async with get_session() as session:
        repo = EvalRepository(session)
        cases, _ = await repo.list_cases(run_id, passed=False, limit=500)
        payload = []
        for case in cases:
            if case.status != "completed":
                continue
            payload.append(
                {
                    "suite": case.suite,
                    "difficulty": case.difficulty,
                    "user_text": case.user_text,
                    "generated_scenario": case.generated_scenario_json,
                    "initial_state": case.initial_state_json,
                    "expected_operations": case.expected_operations_json,
                    "forbidden_operations": case.forbidden_operations_json,
                }
            )
        return {"cases": payload, "export": failed_cases_export(payload)}


@router.post("/{run_id}/rerun-failed")
async def rerun_failed_cases(
    run_id: uuid.UUID,
    user: User = Depends(require_roles("dev", "admin")),
) -> dict[str, str]:
    async with get_session() as session:
        repo = EvalRepository(session)
        run = await session.get(EvalRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        cases, _ = await repo.list_cases(run_id, passed=False, limit=500)
        failed = [c for c in cases if c.status == "completed"]
        config = dict(run.config_json or {})
        config["n_cases"] = len(failed) or 1
        config["rerun_from"] = str(run_id)
        new_run = await repo.create_run(
            name=f"{run.name} (rerun failed)",
            config=config,
            created_by=user.email,
            generator_model=run.generator_model or DEFAULT_GENERATOR_MODEL,
            judge_model=run.judge_model or DEFAULT_JUDGE_MODEL,
            git_commit=_git_commit(),
            total_cases=len(failed) or 1,
        )
        await session.commit()
        return {"run_id": str(new_run.id), "status": new_run.status}


@router.get("/{run_id}/export")
async def export_eval_run(
    run_id: uuid.UUID,
    user: User = Depends(require_roles("dev", "admin")),
    format: Literal["json", "markdown"] = "json",
) -> Any:
    del user
    async with get_session() as session:
        run = await session.get(EvalRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        metrics = run.metrics_summary_json or {}
        if format == "markdown":
            return {"markdown": report_to_markdown(_run_dto(run).model_dump(), metrics)}
        return {"run": _run_dto(run).model_dump(), "metrics": metrics}
