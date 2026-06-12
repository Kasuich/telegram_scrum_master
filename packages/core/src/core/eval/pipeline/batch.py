"""Batch stages eval pipeline."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

from core.eval.deterministic import evaluate_deterministic
from core.eval.generator import generate_scenario, generate_user_text, today_iso
from core.eval.judge import build_judge_errors, run_heuristic_judge, run_llm_judge
from core.eval.metrics import compute_run_metrics
from core.eval.normalizer import normalize_agent_output
from core.eval.pipeline.base import PipelineContext, with_db
from core.eval.redaction import redact_output
from core.eval.schemas import EvalRunConfig, FinalEvaluation
from core.eval.suites import distribute_suites

logger = logging.getLogger(__name__)


class BatchStagesPipeline:
    async def run(self, ctx: PipelineContext) -> None:
        cfg = ctx.config
        current_date = today_iso()
        plan = distribute_suites(cfg.n_cases, cfg.suites)
        case_ids: list[uuid.UUID] = []

        await with_db(ctx, lambda: ctx.repo.set_run_status(ctx.run_id, "generating_scenarios"))
        sem_scenario = asyncio.Semaphore(cfg.scenario_generation_concurrency)

        async def gen_one(i: int, suite: str, difficulty: str) -> uuid.UUID:
            async with sem_scenario:
                if ctx.cancelled():
                    raise asyncio.CancelledError()

                async def _create() -> uuid.UUID:
                    case = await ctx.repo.create_case(
                        ctx.run_id, suite=suite, difficulty=difficulty, current_date=current_date
                    )
                    await ctx.repo.update_case(case.id, status="scenario_generating")
                    return case.id

                case_id = await with_db(ctx, _create)
                scenario = await generate_scenario(
                    suite=suite,
                    difficulty=difficulty,
                    current_date=current_date,
                    model=cfg.generator_model,
                    index=i,
                )

                async def _save_scenario() -> None:
                    await ctx.repo.update_case(
                        case_id,
                        status="scenario_generated",
                        generated_scenario_json=scenario.model_dump(),
                        initial_state_json=scenario.initial_state,
                        expected_operations_json=scenario.expected_operations,
                        forbidden_operations_json=scenario.forbidden_operations,
                        expected_final_state_json=scenario.expected_final_state,
                        metadata_json=scenario.metadata,
                    )

                await with_db(ctx, _save_scenario)
                return case_id

        tasks = [gen_one(i, suite, diff) for i, (suite, diff) in enumerate(plan)]
        case_ids = await asyncio.gather(*tasks, return_exceptions=False)
        await with_db(ctx, lambda: ctx.repo.refresh_run_progress(ctx.run_id))

        await with_db(ctx, lambda: ctx.repo.set_run_status(ctx.run_id, "generating_user_texts"))
        sem_text = asyncio.Semaphore(cfg.user_text_generation_concurrency)

        async def gen_text(case_id: uuid.UUID) -> None:
            async with sem_text:
                if ctx.cancelled():
                    raise asyncio.CancelledError()

                detail = await with_db(ctx, lambda: ctx.repo.get_case_detail(ctx.run_id, case_id))
                if not detail or not detail.generated_scenario_json:
                    return

                await with_db(
                    ctx, lambda: ctx.repo.update_case(case_id, status="user_text_generating")
                )
                from core.eval.schemas import SyntheticScenario

                scenario = SyntheticScenario.model_validate(detail.generated_scenario_json)
                text = await generate_user_text(
                    scenario, model=cfg.generator_model, current_date=current_date
                )

                async def _save_text() -> None:
                    await ctx.repo.update_case(
                        case_id,
                        status="user_text_generated",
                        user_text=text,
                        started_at=datetime.now(timezone.utc),
                    )

                await with_db(ctx, _save_text)

        await asyncio.gather(*[gen_text(cid) for cid in case_ids])
        await with_db(ctx, lambda: ctx.repo.refresh_run_progress(ctx.run_id))

        await with_db(ctx, lambda: ctx.repo.set_run_status(ctx.run_id, "running_agents"))
        sem_agent = asyncio.Semaphore(cfg.agent_concurrency)

        async def run_agent(case_id: uuid.UUID) -> None:
            async with sem_agent:
                if ctx.cancelled():
                    raise asyncio.CancelledError()
                await self._run_agent_case(ctx, case_id, cfg)

        await asyncio.gather(*[run_agent(cid) for cid in case_ids], return_exceptions=True)
        await with_db(ctx, lambda: ctx.repo.refresh_run_progress(ctx.run_id))

        await with_db(ctx, lambda: ctx.repo.set_run_status(ctx.run_id, "judging"))
        sem_judge = asyncio.Semaphore(cfg.judge_concurrency)

        async def judge_case(case_id: uuid.UUID) -> None:
            async with sem_judge:
                if ctx.cancelled():
                    raise asyncio.CancelledError()
                await self._judge_case(ctx, case_id, cfg)

        await asyncio.gather(*[judge_case(cid) for cid in case_ids], return_exceptions=True)
        await self._finalize(ctx)

    async def _run_agent_case(
        self, ctx: PipelineContext, case_id: uuid.UUID, cfg: EvalRunConfig
    ) -> None:
        detail = await with_db(ctx, lambda: ctx.repo.get_case_detail(ctx.run_id, case_id))
        if not detail or not detail.user_text:
            return
        user_text = detail.user_text
        initial_state = detail.initial_state_json or {"tasks": []}
        current_date = detail.current_date or today_iso()
        case_started = time.monotonic()
        await with_db(ctx, lambda: ctx.repo.update_case(case_id, status="agent_running"))
        agent_started_wall = datetime.now(timezone.utc)
        agent_started = time.monotonic()
        eval_mode = "real_tracker" if cfg.use_real_tracker else "dry_run"
        context = {
            "channel": "eval",
            "actor_tracker_login": "eval-user",
            "metadata": {
                "eval_mode": eval_mode,
                "eval_run_id": str(ctx.run_id),
                "eval_case_id": str(case_id),
                "initial_state": initial_state,
                "current_date": current_date,
            },
        }
        try:
            result = await asyncio.wait_for(
                ctx.rpc.invoke_agent(
                    message=user_text,
                    session_id=f"eval-{case_id}",
                    context=context,
                ),
                timeout=cfg.timeout_sec_per_case,
            )
            agent_latency = time.monotonic() - agent_started
            agent_finished_wall = datetime.now(timezone.utc)
            raw = redact_output(result.model_dump()) or {}
            normalized = normalize_agent_output(raw)
            fake_state = None
            if result.eval_artifacts:
                fake_state = result.eval_artifacts.get("final_fake_tracker_state")

            async def _save_agent_ok() -> None:
                await ctx.repo.update_case(case_id, status="agent_completed")
                await ctx.repo.update_case_result(
                    case_id,
                    status="agent_completed",
                    agent_raw_output_json=raw,
                    agent_normalized_output_json=normalized.model_dump(),
                    final_fake_tracker_state_json=fake_state,
                    agent_latency_sec=agent_latency,
                    agent_started_at=agent_started_wall,
                    agent_finished_at=agent_finished_wall,
                )

            await with_db(ctx, _save_agent_ok)
        except TimeoutError:
            agent_lat = time.monotonic() - agent_started
            finished = datetime.now(timezone.utc)

            async def _save_timeout() -> None:
                await ctx.repo.update_case(case_id, status="timeout", finished_at=finished)
                await ctx.repo.update_case_result(
                    case_id,
                    status="timeout",
                    technical_error="agent timeout",
                    agent_latency_sec=agent_lat,
                    latency_sec=time.monotonic() - case_started,
                )

            await with_db(ctx, _save_timeout)
        except Exception as exc:
            logger.exception("Agent case failed %s", case_id)
            agent_lat = time.monotonic() - agent_started
            finished = datetime.now(timezone.utc)
            error_text = str(exc)

            async def _save_failed() -> None:
                await ctx.repo.update_case(case_id, status="failed", finished_at=finished)
                await ctx.repo.update_case_result(
                    case_id,
                    status="failed",
                    technical_error=error_text,
                    agent_latency_sec=agent_lat,
                    latency_sec=time.monotonic() - case_started,
                )

            await with_db(ctx, _save_failed)

    async def _judge_case(
        self, ctx: PipelineContext, case_id: uuid.UUID, cfg: EvalRunConfig
    ) -> None:
        detail = await with_db(ctx, lambda: ctx.repo.get_case_detail(ctx.run_id, case_id))
        if not detail or not detail.result:
            return
        if detail.status in {"timeout", "failed", "cancelled"}:
            return
        case_started = detail.started_at
        total_start = time.monotonic()

        async def _mark_judging() -> None:
            await ctx.repo.update_case(case_id, status="judging")
            await ctx.repo.update_case_result(case_id, status="judging")

        await with_db(ctx, _mark_judging)

        normalized_raw = detail.result.agent_normalized_output_json or {"operations": []}
        from core.eval.schemas import NormalizedAgentOutput

        normalized = NormalizedAgentOutput.model_validate(normalized_raw)
        det = evaluate_deterministic(
            normalized,
            detail.expected_operations_json,
            detail.forbidden_operations_json,
        )
        judge_eval = None
        judge_latency = None
        fake_state = detail.result.final_fake_tracker_state_json if detail.result else None
        scenario = detail.generated_scenario_json or {}
        t0 = time.monotonic()
        if cfg.use_llm_judge:
            judge_eval = await run_llm_judge(
                user_text=detail.user_text or "",
                scenario=scenario,
                expected_operations=detail.expected_operations_json,
                forbidden_operations=detail.forbidden_operations_json,
                initial_state=detail.initial_state_json,
                expected_final_state=detail.expected_final_state_json,
                normalized=normalized,
                final_fake_tracker_state=fake_state,
                model=cfg.judge_model,
            )
        else:
            judge_eval = run_heuristic_judge(
                user_text=detail.user_text or "",
                scenario=scenario,
                expected_operations=detail.expected_operations_json,
                forbidden_operations=detail.forbidden_operations_json,
                normalized=normalized,
                final_fake_tracker_state=fake_state,
            )
        judge_latency = time.monotonic() - t0

        if judge_eval.technical_error:
            passed = False
            score = 0.0
            errors = [judge_eval.technical_error]
        else:
            passed = judge_eval.passed
            score = judge_eval.score
            errors = build_judge_errors(judge_eval.criteria, judge_eval.explanation)

        final = FinalEvaluation(
            passed=passed,
            score=score,
            weighted_score=judge_eval.weighted_score,
            criteria=judge_eval.criteria,
            errors=errors,
        )
        latency = time.monotonic() - total_start
        if case_started:
            latency = (datetime.now(timezone.utc) - case_started).total_seconds()

        agent_lat = detail.result.agent_latency_sec
        total_latency = (agent_lat or 0) + (judge_latency or 0)

        finished_at = datetime.now(timezone.utc)

        async def _save_judge() -> None:
            await ctx.repo.update_case(case_id, status="completed", finished_at=finished_at)
            await ctx.repo.update_case_result(
                case_id,
                status="completed",
                passed=passed,
                score=score,
                deterministic_evaluation_json=det.model_dump(),
                llm_judge_evaluation_json=judge_eval.model_dump() if judge_eval else None,
                final_evaluation_json=final.model_dump(),
                judge_latency_sec=judge_latency,
                latency_sec=total_latency or latency,
            )

        await with_db(ctx, _save_judge)

    async def _finalize(self, ctx: PipelineContext) -> None:
        rows = await with_db(ctx, lambda: ctx.repo.collect_case_metric_rows(ctx.run_id))
        metrics = compute_run_metrics(rows)

        async def _save_run_metrics() -> None:
            await ctx.repo.update_run_counters(
                ctx.run_id,
                pass_rate=metrics.get("pass_rate"),
                avg_latency_sec=metrics.get("avg_latency_sec"),
                p95_latency_sec=metrics.get("p95_latency_sec"),
                avg_agent_latency_sec=metrics.get("avg_agent_latency_sec"),
                p95_agent_latency_sec=metrics.get("p95_agent_latency_sec"),
                metrics_summary_json=metrics,
                error_summary_json={"top_errors": metrics.get("top_errors")},
                completed_cases=metrics.get("completed_cases"),
                passed_cases=metrics.get("passed_cases"),
                failed_cases=metrics.get("failed_cases"),
                timeout_cases=metrics.get("timeout_cases"),
            )
            metric_rows = [
                ("pass_rate", float(metrics.get("pass_rate") or 0), None),
                ("avg_agent_latency_sec", float(metrics.get("avg_agent_latency_sec") or 0), None),
                ("p95_agent_latency_sec", float(metrics.get("p95_agent_latency_sec") or 0), None),
                ("avg_weighted_score", float(metrics.get("avg_weighted_score") or 0), None),
            ]
            for criterion, value in (metrics.get("criteria_avg") or {}).items():
                metric_rows.append(("criteria_avg", float(value), {"criterion": str(criterion)}))
            for suite, stats in (metrics.get("agent_latency_by_suite") or {}).items():
                if stats.get("avg") is not None:
                    metric_rows.append(("agent_latency_avg", float(stats["avg"]), {"suite": suite}))
            await ctx.repo.save_metrics(ctx.run_id, metric_rows)
            has_errors = (metrics.get("failed_cases") or 0) > 0 or (
                metrics.get("timeout_cases") or 0
            ) > 0
            status = "completed_with_errors" if has_errors else "completed"
            await ctx.repo.set_run_status(ctx.run_id, status)
            await ctx.repo.log_event(ctx.run_id, "run_completed", f"Run finished: {status}")

        await with_db(ctx, _save_run_metrics)
