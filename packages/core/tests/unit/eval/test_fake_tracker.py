"""Tests for fake Tracker store."""

from __future__ import annotations

import pytest
from core.eval.fake_tracker import FakeTrackerStore


@pytest.mark.asyncio
async def test_create_and_search() -> None:
    store = FakeTrackerStore(queue="TEST", initial_state={"tasks": []})
    created = await store.create_issue({"queue": "TEST", "summary": "Bug"})
    assert created["key"].startswith("TEST-")
    result = store.search_issues_normalized("Bug")
    assert result["count"] >= 1


@pytest.mark.asyncio
async def test_seed_existing_task() -> None:
    store = FakeTrackerStore(
        queue="SUPPORT",
        initial_state={
            "tasks": [{"key": "SUPPORT-101", "summary": "Auth", "status": "open"}],
        },
    )
    issue = await store.request("GET", "/issues/SUPPORT-101")
    assert issue["key"] == "SUPPORT-101"
    found = store.search_issues_normalized("Auth")
    assert found["count"] == 1
