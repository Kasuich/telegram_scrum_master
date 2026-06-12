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
async def test_no_latency_by_default() -> None:
    store = FakeTrackerStore(queue="TEST")
    await store.mcp_call("CreateIssue", {"summary": "a"})
    summary = store.latency_summary()
    assert summary["enabled"] is False
    assert summary["calls"] == 0


@pytest.mark.asyncio
async def test_latency_is_seeded_and_recorded() -> None:
    from core.eval.tracker_profile import ToolLatencyProfile

    def make() -> FakeTrackerStore:
        # tiny scale keeps the test instant while preserving the seeded sequence
        return FakeTrackerStore(
            queue="TEST", latency_profile=ToolLatencyProfile(scale=0.001), seed="case-1"
        )

    async def run(store: FakeTrackerStore) -> dict:
        await store.mcp_call("CreateIssue", {"summary": "a"})
        await store.mcp_call("GetIssues", {"query": "a"})
        await store.mcp_call("GetIssue", {"issueKey": "TEST-1"})
        return store.latency_summary()

    s1 = await run(make())
    s2 = await run(make())
    assert s1["enabled"] is True
    assert s1["calls"] == 3
    assert set(s1["by_op"]) == {"create_issue", "search", "get_issue"}
    # Same seed → identical replay; different ops have different op buckets.
    assert s1["total_sec"] == s2["total_sec"]


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
