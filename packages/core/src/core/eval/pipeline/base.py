"""Pipeline base types."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

from core.eval.repository import EvalRepository
from core.eval.rpc_client import OrchestratorRpcClient
from core.eval.schemas import EvalRunConfig

T = TypeVar("T")


@dataclass
class PipelineContext:
    run_id: uuid.UUID
    config: EvalRunConfig
    repo: EvalRepository
    rpc: OrchestratorRpcClient
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    db_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()


async def with_db(ctx: PipelineContext, fn: Callable[[], Awaitable[T]]) -> T:
    """Serialize DB access — AsyncSession is not safe for concurrent use."""
    async with ctx.db_lock:
        return await fn()
