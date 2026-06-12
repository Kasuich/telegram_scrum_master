"""Poll DB for queued eval runs and execute them."""

from __future__ import annotations

import asyncio
import logging
import uuid

from core.db import get_session
from core.eval.repository import EvalRepository
from core.eval.runner import EvalRunExecutor

logger = logging.getLogger(__name__)


class EvalRunnerDaemon:
    def __init__(self, orchestrator_url: str, *, poll_interval: float = 2.0) -> None:
        self._executor = EvalRunExecutor(orchestrator_url)
        self._poll_interval = poll_interval
        self._running_tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        logger.info("EvalRunnerDaemon started")
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                logger.info("EvalRunnerDaemon stopped")
                raise
            except Exception:
                logger.exception("EvalRunnerDaemon tick error")
            await asyncio.sleep(self._poll_interval)

    async def _tick(self) -> None:
        run_id: uuid.UUID | None = None
        async with get_session() as session:
            repo = EvalRepository(session)
            run = await repo.claim_queued_run()
            if run:
                run_id = run.id
            await session.commit()

        if run_id is None:
            return

        task = asyncio.create_task(self._executor.execute_run(run_id))
        self._running_tasks.add(task)
        task.add_done_callback(self._running_tasks.discard)
