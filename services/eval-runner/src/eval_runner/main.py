"""Eval runner service."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from core.db import create_all_tables
from fastapi import FastAPI

from eval_runner.daemon import EvalRunnerDaemon


def _orchestrator_url() -> str:
    return os.getenv("ORCHESTRATOR_URL", "http://pm-orchestrator:8001").rstrip("/")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await create_all_tables()
    daemon = EvalRunnerDaemon(_orchestrator_url())
    task = asyncio.create_task(daemon.run())
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


app = FastAPI(title="eval-runner", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "eval-runner"}
