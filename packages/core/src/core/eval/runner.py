"""Eval run executor daemon helpers."""

from __future__ import annotations

import asyncio
import logging
import uuid

from core.db import get_session
from core.eval.pipeline.base import PipelineContext
from core.eval.pipeline.batch import BatchStagesPipeline
from core.eval.repository import EvalRepository
from core.eval.rpc_client import OrchestratorRpcClient
from core.eval.schemas import EvalRunConfig

logger = logging.getLogger(__name__)

_active_runs: dict[uuid.UUID, asyncio.Event] = {}


class EvalRunExecutor:
    def __init__(self, orchestrator_url: str) -> None:
        self._orchestrator_url = orchestrator_url
        self._pipeline = BatchStagesPipeline()

    def cancel(self, run_id: uuid.UUID) -> None:
        ev = _active_runs.get(run_id)
        if ev:
            ev.set()

    async def execute_run(self, run_id: uuid.UUID) -> None:
        cancel_event = asyncio.Event()
        _active_runs[run_id] = cancel_event
        try:
            async with get_session() as session:
                repo = EvalRepository(session)
                run = await repo.get_run(run_id)
                if not run:
                    return
                config = EvalRunConfig.model_validate(run.config_json or {})
                ctx = PipelineContext(
                    run_id=run_id,
                    config=config,
                    repo=repo,
                    rpc=OrchestratorRpcClient(self._orchestrator_url),
                    cancel_event=cancel_event,
                )
                try:
                    await self._pipeline.run(ctx)
                except asyncio.CancelledError:
                    await repo.mark_cancelled(run_id)
                if await repo.is_cancelled(run_id):
                    await repo.mark_cancelled(run_id)
                await session.commit()
        except Exception:
            logger.exception("Eval run %s failed", run_id)
            async with get_session() as session:
                repo = EvalRepository(session)
                await repo.set_run_status(run_id, "failed")
                await repo.log_event(run_id, "run_failed", "Run failed", level="error")
                await session.commit()
        finally:
            _active_runs.pop(run_id, None)
