"""Database repository for eval harness."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.models import EvalCase, EvalCaseResult, EvalEvent, EvalMetric, EvalRun


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EvalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_run(
        self,
        *,
        name: str,
        config: dict[str, Any],
        created_by: str | None,
        generator_model: str,
        judge_model: str,
        git_commit: str | None,
        total_cases: int,
    ) -> EvalRun:
        run = EvalRun(
            name=name,
            status="queued",
            created_by=created_by,
            config_json=config,
            generator_model=generator_model,
            judge_model=judge_model,
            git_commit=git_commit,
            total_cases=total_cases,
        )
        self.session.add(run)
        await self.session.flush()
        await self.log_event(run.id, "run_created", f"Eval run {name} queued", level="info")
        return run

    async def get_run(self, run_id: uuid.UUID) -> EvalRun | None:
        return await self.session.get(EvalRun, run_id)

    async def claim_queued_run(self) -> EvalRun | None:
        stmt = (
            select(EvalRun)
            .where(EvalRun.status == "queued")
            .order_by(EvalRun.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        result = await self.session.execute(stmt)
        run = result.scalar_one_or_none()
        if run is None:
            return None
        run.status = "generating_scenarios"
        run.started_at = _utcnow()
        await self.session.flush()
        return run

    async def set_run_status(self, run_id: uuid.UUID, status: str) -> None:
        run = await self.session.get(EvalRun, run_id)
        if run:
            run.status = status
            if status in {"completed", "completed_with_errors", "failed", "cancelled"}:
                run.finished_at = _utcnow()

    async def update_run_counters(self, run_id: uuid.UUID, **fields: Any) -> None:
        run = await self.session.get(EvalRun, run_id)
        if not run:
            return
        for key, value in fields.items():
            if hasattr(run, key):
                setattr(run, key, value)

    async def refresh_run_progress(self, run_id: uuid.UUID) -> None:
        run = await self.session.get(EvalRun, run_id)
        if not run:
            return
        total = await self.session.scalar(
            select(func.count()).select_from(EvalCase).where(EvalCase.run_id == run_id)
        )
        generated = await self.session.scalar(
            select(func.count())
            .select_from(EvalCase)
            .where(EvalCase.run_id == run_id, EvalCase.user_text.is_not(None))
        )
        completed = await self.session.scalar(
            select(func.count())
            .select_from(EvalCase)
            .where(EvalCase.run_id == run_id, EvalCase.status == "completed")
        )
        failed = await self.session.scalar(
            select(func.count())
            .select_from(EvalCaseResult)
            .where(
                EvalCaseResult.run_id == run_id,
                EvalCaseResult.passed.is_(False),
                EvalCaseResult.status == "completed",
            )
        )
        passed = await self.session.scalar(
            select(func.count())
            .select_from(EvalCaseResult)
            .where(EvalCaseResult.run_id == run_id, EvalCaseResult.passed.is_(True))
        )
        timeouts = await self.session.scalar(
            select(func.count())
            .select_from(EvalCase)
            .where(EvalCase.run_id == run_id, EvalCase.status == "timeout")
        )
        run.generated_cases = int(generated or 0)
        run.completed_cases = int(completed or 0)
        run.failed_cases = int(failed or 0)
        run.passed_cases = int(passed or 0)
        run.timeout_cases = int(timeouts or 0)
        run.total_cases = int(total or run.total_cases)
        if run.completed_cases:
            run.pass_rate = (run.passed_cases or 0) / run.completed_cases

    async def create_case(
        self,
        run_id: uuid.UUID,
        *,
        suite: str,
        difficulty: str,
        current_date: str,
    ) -> EvalCase:
        case = EvalCase(
            run_id=run_id,
            suite=suite,
            difficulty=difficulty,
            current_date=current_date,
            status="queued",
        )
        self.session.add(case)
        await self.session.flush()
        result = EvalCaseResult(run_id=run_id, case_id=case.id, status="queued")
        self.session.add(result)
        await self.session.flush()
        return case

    async def update_case(self, case_id: uuid.UUID, **fields: Any) -> None:
        case = await self.session.get(EvalCase, case_id)
        if case:
            for key, value in fields.items():
                if hasattr(case, key):
                    setattr(case, key, value)

    async def update_case_result(self, case_id: uuid.UUID, **fields: Any) -> None:
        stmt = select(EvalCaseResult).where(EvalCaseResult.case_id == case_id)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row:
            for key, value in fields.items():
                if hasattr(row, key):
                    setattr(row, key, value)

    async def log_event(
        self,
        run_id: uuid.UUID,
        event_type: str,
        message: str,
        *,
        case_id: uuid.UUID | None = None,
        level: str = "info",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            EvalEvent(
                run_id=run_id,
                case_id=case_id,
                level=level,
                event_type=event_type,
                message=message,
                payload_json=payload,
            )
        )

    async def list_cases(
        self,
        run_id: uuid.UUID,
        *,
        suite: str | None = None,
        status: str | None = None,
        passed: bool | None = None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[EvalCase], int]:
        stmt: Select[Any] = (
            select(EvalCase)
            .where(EvalCase.run_id == run_id)
            .options(selectinload(EvalCase.result))
            .order_by(EvalCase.created_at)
        )
        if suite:
            stmt = stmt.where(EvalCase.suite == suite)
        if status:
            stmt = stmt.where(EvalCase.status == status)
        if search:
            stmt = stmt.where(EvalCase.user_text.ilike(f"%{search}%"))
        if passed is not None:
            stmt = stmt.join(EvalCaseResult).where(EvalCaseResult.passed.is_(passed))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await self.session.scalar(count_stmt)) or 0)
        rows = (await self.session.execute(stmt.offset(offset).limit(limit))).scalars().all()
        return list(rows), total

    async def get_case_detail(self, run_id: uuid.UUID, case_id: uuid.UUID) -> EvalCase | None:
        stmt = (
            select(EvalCase)
            .where(EvalCase.run_id == run_id, EvalCase.id == case_id)
            .options(selectinload(EvalCase.result))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def save_metrics(
        self, run_id: uuid.UUID, metrics: list[tuple[str, float, dict[str, Any] | None]]
    ) -> None:
        for name, value, dims in metrics:
            self.session.add(
                EvalMetric(
                    run_id=run_id, metric_name=name, metric_value=value, dimensions_json=dims
                )
            )

    async def request_cancel(self, run_id: uuid.UUID) -> bool:
        run = await self.session.get(EvalRun, run_id)
        if not run or run.status in {"completed", "failed", "cancelled"}:
            return False
        run.status = "cancelling"
        return True

    async def is_cancelled(self, run_id: uuid.UUID) -> bool:
        run = await self.session.get(EvalRun, run_id)
        return run is not None and run.status in {"cancelling", "cancelled"}

    async def mark_cancelled(self, run_id: uuid.UUID) -> None:
        await self.session.execute(
            update(EvalCase)
            .where(
                EvalCase.run_id == run_id,
                EvalCase.status.not_in(["completed", "failed", "timeout"]),
            )
            .values(status="cancelled")
        )
        await self.set_run_status(run_id, "cancelled")

    async def collect_case_metric_rows(self, run_id: uuid.UUID) -> list[dict[str, Any]]:
        stmt = (
            select(EvalCase, EvalCaseResult)
            .join(EvalCaseResult, EvalCaseResult.case_id == EvalCase.id)
            .where(EvalCase.run_id == run_id)
        )
        rows = []
        for case, result in (await self.session.execute(stmt)).all():
            rows.append(
                {
                    "suite": case.suite,
                    "status": case.status,
                    "passed": result.passed,
                    "score": result.score,
                    "latency_sec": result.latency_sec,
                    "agent_latency_sec": result.agent_latency_sec,
                    "final_evaluation": result.final_evaluation_json,
                    "deterministic_evaluation": result.deterministic_evaluation_json,
                    "llm_judge_evaluation": result.llm_judge_evaluation_json,
                }
            )
        return rows
