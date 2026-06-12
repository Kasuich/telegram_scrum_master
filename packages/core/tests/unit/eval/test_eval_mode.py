"""Tests for eval mode and orchestrator fake tracker hooks."""

from __future__ import annotations

import pytest
from core.eval.fake_tracker import get_fake_tracker_store
from core.eval.mode import (
    activate_fake_tracker,
    deactivate_fake_tracker,
    eval_runtime_overlay,
    is_eval_dry_run,
)
from core.invocation import InvocationContext


def test_eval_runtime_overlay_skips_confirm() -> None:
    rc = eval_runtime_overlay(None)
    assert rc.skip_tool_confirm is True
    assert "high" in rc.auto_risk


def test_is_eval_dry_run() -> None:
    ctx = InvocationContext(metadata={"eval_mode": "dry_run"})
    assert is_eval_dry_run(ctx) is True


@pytest.mark.asyncio
async def test_fake_tracker_activate_deactivate() -> None:
    ctx = InvocationContext(
        metadata={
            "eval_mode": "dry_run",
            "initial_state": {"tasks": [{"key": "TEST-1", "summary": "A", "status": "open"}]},
        }
    )
    store, token = activate_fake_tracker(ctx, default_queue="TEST")
    assert store is not None
    assert get_fake_tracker_store() is store
    issue = await store.request("GET", "/issues/TEST-1")
    assert issue["key"] == "TEST-1"
    deactivate_fake_tracker(token)
    assert get_fake_tracker_store() is None
