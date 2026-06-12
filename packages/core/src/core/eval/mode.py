"""Eval mode helpers for orchestrator integration."""

from __future__ import annotations

from contextvars import Token

from core.config import RuntimeConfig
from core.eval.fake_tracker import (
    FakeTrackerStore,
    reset_fake_tracker_store,
    seed_fake_tracker_from_metadata,
    set_fake_tracker_store,
)
from core.invocation import InvocationContext


def is_eval_mode(ctx: InvocationContext | None) -> bool:
    if ctx is None:
        return False
    mode = (ctx.metadata or {}).get("eval_mode")
    return mode in {"dry_run", "real_tracker"}


def is_eval_dry_run(ctx: InvocationContext | None) -> bool:
    if ctx is None:
        return False
    return (ctx.metadata or {}).get("eval_mode") == "dry_run"


def eval_runtime_overlay(base: RuntimeConfig | None) -> RuntimeConfig:
    """Runtime config for eval: auto-approve all tool risks."""
    if base is not None:
        data = base.model_dump()
    else:
        data = {}
    data["skip_tool_confirm"] = True
    data["auto_risk"] = ["low", "medium", "high"]
    data["confirm_risk"] = []
    data["always_confirm_tools"] = []
    return RuntimeConfig(**data)


def activate_fake_tracker(
    ctx: InvocationContext | None,
    *,
    default_queue: str,
) -> tuple[FakeTrackerStore | None, Token | None]:
    if not is_eval_dry_run(ctx):
        return None, None
    metadata = (ctx.metadata if ctx else {}) or {}
    store = seed_fake_tracker_from_metadata(metadata, default_queue=default_queue)
    token = set_fake_tracker_store(store)
    return store, token


def deactivate_fake_tracker(token: Token | None) -> None:
    if token is not None:
        reset_fake_tracker_store(token)
