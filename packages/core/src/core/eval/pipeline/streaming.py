"""Streaming pipeline mode (Phase 2 stub)."""

from __future__ import annotations

from core.eval.pipeline.base import PipelineContext


class StreamingPipeline:
    """Per-case streaming pipeline — not implemented in MVP."""

    async def run(self, ctx: PipelineContext) -> None:
        raise NotImplementedError("Streaming pipeline is planned for Phase 2")
