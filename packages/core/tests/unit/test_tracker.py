"""
Unit tests for TrackerClient and tracker_tools.
All HTTP calls are mocked — no real Tracker access needed.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from core.tracker import TrackerClient, TrackerError

ENV = {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test_db",
    "YC_API_KEY": "test_api_key_12345678901234567890",
    "YC_FOLDER_ID": "b1g1234567890abcdef",
    "TRACKER_TOKEN": "test_tracker_token_12345678901234567890",
    "TRACKER_ORG_ID": "12345678901234567890",
    "TRACKER_ORG_TYPE": "cloud",
    "TRACKER_QUEUE": "TEST",
}

ISSUE_RESPONSE = {
    "key": "TEST-1",
    "summary": "Fix login bug",
    "status": {"display": "Открыт"},
    "priority": {"display": "Критический"},
    "assignee": {"display": "Alice"},
    "description": "Details here",
}

COMMENT_RESPONSE = {"id": "42", "text": "Hello!"}


def _ok(data: Any, status: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = data
    resp.text = json.dumps(data)
    return resp


def _err(status: int, message: str = "error") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = {"errorMessages": [message]}
    resp.text = json.dumps({"errorMessages": [message]})
    return resp


def _client() -> TrackerClient:
    return TrackerClient(
        token="test_token",
        org_id="test_org",
        org_type="cloud",
        base_url="https://api.tracker.yandex.net/v3/",
    )


def _patch_request(return_value: Any = None, side_effect: Any = None):
    """Patch httpx.AsyncClient.request used inside TrackerClient._http."""
    if side_effect is not None:
        return patch("httpx.AsyncClient.request", AsyncMock(side_effect=side_effect))
    return patch("httpx.AsyncClient.request", AsyncMock(return_value=return_value))


# ---------------------------------------------------------------------------
# TrackerClient — headers
# ---------------------------------------------------------------------------


_BASE = "https://api.tracker.yandex.net/v3/"


class TestTrackerClientHeaders:
    def test_cloud_org_uses_correct_header(self):
        c = TrackerClient(token="tok", org_id="org1", org_type="cloud", base_url=_BASE)
        hdrs = c._headers()
        assert hdrs["X-Cloud-Org-ID"] == "org1"
        assert "X-Org-ID" not in hdrs

    def test_360_org_uses_correct_header(self):
        c = TrackerClient(token="tok", org_id="org1", org_type="360", base_url=_BASE)
        hdrs = c._headers()
        assert hdrs["X-Org-ID"] == "org1"
        assert "X-Cloud-Org-ID" not in hdrs

    def test_auth_header(self):
        c = TrackerClient(token="my_token", org_id="x", org_type="360", base_url=_BASE)
        assert c._headers()["Authorization"] == "OAuth my_token"


# ---------------------------------------------------------------------------
# TrackerClient — HTTP error handling
# ---------------------------------------------------------------------------


class TestTrackerClientErrors:
    async def test_403_raises_tracker_error(self):
        c = _client()
        with _patch_request(_err(403, "Access denied")):
            with pytest.raises(TrackerError, match="Access denied"):
                await c._request("GET", "/issues/X-1")

    async def test_404_raises_tracker_error(self):
        c = _client()
        with _patch_request(_err(404)):
            with pytest.raises(TrackerError) as exc_info:
                await c._request("GET", "/issues/X-1")
            assert exc_info.value.status_code == 404

    async def test_500_raises_tracker_error(self):
        c = _client()
        with _patch_request(_err(500, "Internal error")):
            with pytest.raises(TrackerError) as exc_info:
                await c._request("GET", "/issues/X-1")
            assert exc_info.value.status_code == 500

    async def test_204_returns_none(self):
        c = _client()
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 204
        with _patch_request(resp):
            result = await c._request("DELETE", "/issues/X-1")
        assert result is None


# ---------------------------------------------------------------------------
# TrackerClient — CRUD operations
# ---------------------------------------------------------------------------


class TestGetIssue:
    async def test_returns_issue_data(self):
        c = _client()
        with _patch_request(_ok(ISSUE_RESPONSE)):
            result = await c.get_issue("TEST-1")
        assert result["key"] == "TEST-1"
        assert result["summary"] == "Fix login bug"

    async def test_calls_correct_endpoint(self):
        c = _client()
        with _patch_request(_ok(ISSUE_RESPONSE)) as mock_req:
            await c.get_issue("TEST-42")
        url = mock_req.call_args[0][1]
        assert "issues/TEST-42" in url


class TestCreateIssue:
    async def test_creates_with_required_fields(self):
        c = _client()
        with _patch_request(_ok(ISSUE_RESPONSE)) as mock_req:
            result = await c.create_issue("TEST", "Fix login bug")
        body = mock_req.call_args[1]["json"]
        assert body["queue"] == "TEST"
        assert body["summary"] == "Fix login bug"
        assert result["key"] == "TEST-1"

    async def test_optional_fields_included_when_set(self):
        c = _client()
        with _patch_request(_ok(ISSUE_RESPONSE)) as mock_req:
            await c.create_issue(
                "TEST", "Bug", description="desc", priority="critical", assignee="alice"
            )
        body = mock_req.call_args[1]["json"]
        assert body["description"] == "desc"
        assert body["priority"] == "critical"
        assert body["assignee"] == "alice"

    async def test_none_fields_not_included(self):
        c = _client()
        with _patch_request(_ok(ISSUE_RESPONSE)) as mock_req:
            await c.create_issue("TEST", "Bug")
        body = mock_req.call_args[1]["json"]
        assert "description" not in body
        assert "priority" not in body


class TestUpdateIssue:
    async def test_patches_provided_fields(self):
        c = _client()
        with _patch_request(_ok(ISSUE_RESPONSE)) as mock_req:
            await c.update_issue("TEST-1", summary="New summary")
        method, url = mock_req.call_args[0]
        assert method == "PATCH"
        assert "TEST-1" in url
        assert mock_req.call_args[1]["json"] == {"summary": "New summary"}


class TestCommentIssue:
    async def test_posts_comment(self):
        c = _client()
        with _patch_request(_ok(COMMENT_RESPONSE)) as mock_req:
            result = await c.comment_issue("TEST-1", "Hello!")
        assert result["id"] == "42"
        body = mock_req.call_args[1]["json"]
        assert body["text"] == "Hello!"


class TestSearchIssues:
    async def test_returns_list(self):
        c = _client()
        issues = [ISSUE_RESPONSE, {**ISSUE_RESPONSE, "key": "TEST-2"}]
        with _patch_request(_ok(issues)):
            result = await c.search_issues("Status: Open")
        assert len(result) == 2

    async def test_queue_prepended_to_query(self):
        c = _client()
        with _patch_request(_ok([])) as mock_req:
            await c.search_issues("Status: Open", queue="TEST")
        body = mock_req.call_args[1]["json"]
        assert 'Queue: "TEST"' in body["query"]

    async def test_returns_empty_list_on_non_list_response(self):
        c = _client()
        with _patch_request(_ok({})):
            result = await c.search_issues("Status: Open")
        assert result == []


class TestListTransitions:
    async def test_returns_transition_list(self):
        c = _client()
        transitions = [{"id": "start", "display": "In progress"}]
        with _patch_request(_ok(transitions)):
            result = await c.list_transitions("TEST-1")
        assert result[0]["id"] == "start"


class TestTransitionIssue:
    async def test_executes_matching_transition(self):
        c = _client()
        transitions = [{"id": "close", "display": "Закрыт"}]
        with _patch_request(side_effect=[_ok(transitions), _ok({"status": "closed"})]):
            result = await c.transition_issue("TEST-1", "close")
        assert result == {"status": "closed"}

    async def test_executes_with_resolution_in_body(self):
        c = _client()
        transitions = [{"id": "close", "display": "Закрыт"}]
        mock_request = AsyncMock(side_effect=[_ok(transitions), _ok({"status": "closed"})])
        with patch("httpx.AsyncClient.request", mock_request):
            await c.transition_issue("TEST-1", "close", resolution="fixed")
        assert mock_request.call_args_list[1][1]["json"] == {"resolution": "fixed"}

    async def test_raises_if_transition_not_found(self):
        c = _client()
        transitions = [{"id": "reopen", "display": "Reopened"}]
        with _patch_request(_ok(transitions)):
            with pytest.raises(TrackerError, match="not found"):
                await c.transition_issue("TEST-1", "close")


class TestFollowers:
    async def test_followers_add(self):
        c = _client()
        with _patch_request(_ok(ISSUE_RESPONSE)) as mock_req:
            await c.followers_add("TEST-1", ["alice", "bob"])
        body = mock_req.call_args[1]["json"]
        assert body == {"followers": {"add": ["alice", "bob"]}}


class TestCreateIssueExtended:
    async def test_extended_fields(self):
        c = _client()
        with _patch_request(_ok(ISSUE_RESPONSE)) as mock_req:
            await c.create_issue(
                "TEST",
                "Task",
                deadline="2026-06-20",
                followers=["petrov"],
                story_points=3,
                parent="TEST-0",
            )
        body = mock_req.call_args[1]["json"]
        assert body["deadline"] == "2026-06-20"
        assert body["followers"] == ["petrov"]
        assert body["storyPoints"] == 3
        assert body["parent"] == "TEST-0"


class TestGetQueueMeta:
    async def test_combines_queue_and_fields(self):
        c = _client()
        queue_data = {
            "key": "TEST",
            "name": "Test Queue",
            "issueTypes": [{"id": "1", "key": "task", "name": "Task"}],
        }
        local_fields = [{"id": "sp", "name": "Story Points", "schema": {"type": "float"}}]
        resolutions = [{"id": "1", "key": "fixed", "name": "Решён"}]
        with _patch_request(side_effect=[_ok(queue_data), _ok(local_fields), _ok(resolutions)]):
            meta = await c.get_queue_meta("TEST")
        assert meta["queue_key"] == "TEST"
        assert len(meta["local_fields"]) == 1
        assert meta["resolutions"][0]["key"] == "fixed"


# ---------------------------------------------------------------------------
# tracker_tools — via @platform_tool
# ---------------------------------------------------------------------------


class TestTrackerTools:
    @patch.dict("os.environ", ENV)
    async def test_tracker_get_issue_tool(self):
        from core.tracker_tools import tracker_get_issue

        with patch("core.tracker.TrackerClient._request", AsyncMock(return_value=ISSUE_RESPONSE)):
            result = await tracker_get_issue("TEST-1")

        assert result["key"] == "TEST-1"
        assert result["summary"] == "Fix login bug"
        assert result["status"] == "Открыт"

    @patch.dict("os.environ", ENV)
    async def test_tracker_create_issue_tool(self):
        from core.tracker_tools import tracker_create_issue

        with patch("core.tracker_tools.TrackerClient") as mock_cls:
            client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = client
            client.search_issues.return_value = []
            client.create_issue.return_value = ISSUE_RESPONSE
            result = await tracker_create_issue("Fix login bug", queue="TEST")

        assert result["key"] == "TEST-1"
        assert "url" in result

    @patch.dict("os.environ", ENV)
    async def test_tracker_create_issue_returns_existing_duplicate(self):
        from core.tracker_tools import tracker_create_issue

        existing = {
            **ISSUE_RESPONSE,
            "key": "TEST-99",
            "summary": "Fix login bug",
            "status": {"display": "Закрыт", "key": "closed"},
            "type": {"key": "task"},
        }
        with patch("core.tracker_tools.TrackerClient") as mock_cls:
            client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = client
            client.search_issues.return_value = [existing]
            result = await tracker_create_issue("Fix login bug", queue="TEST")

        assert result["key"] == "TEST-99"
        assert result.get("skipped_duplicate") is True
        client.create_issue.assert_not_called()

    @patch.dict("os.environ", ENV)
    async def test_tracker_search_tool(self):
        from core.tracker_tools import tracker_search_issues

        with patch(
            "core.tracker.TrackerClient._request",
            AsyncMock(return_value=[ISSUE_RESPONSE]),
        ):
            result = await tracker_search_issues("Status: Open", queue="TEST")

        assert result["count"] == 1
        assert result["issues"][0]["key"] == "TEST-1"

    @patch.dict("os.environ", ENV)
    async def test_tracker_comment_tool(self):
        from core.tracker_tools import tracker_comment_issue

        with patch("core.tracker.TrackerClient._request", AsyncMock(return_value=COMMENT_RESPONSE)):
            result = await tracker_comment_issue("TEST-1", "Hello!")

        assert result["comment_id"] == "42"
        assert result["issue_key"] == "TEST-1"

    @patch.dict("os.environ", ENV)
    async def test_tracker_update_tool_no_fields(self):
        from core.tracker_tools import tracker_update_issue

        result = await tracker_update_issue("TEST-1")
        assert "error" in result

    @patch.dict("os.environ", ENV)
    async def test_tracker_list_transitions_tool(self):
        from core.tracker_tools import tracker_list_transitions

        transitions = [{"id": "start", "display": "Start", "to": {"display": "Open"}}]
        with patch(
            "core.tracker.TrackerClient._request",
            AsyncMock(return_value=transitions),
        ):
            result = await tracker_list_transitions("TEST-1")
        assert result["transitions"][0]["id"] == "start"

    @patch.dict("os.environ", ENV)
    async def test_tracker_transition_blocks_close(self):
        from core.tracker_tools import tracker_transition_issue

        result = await tracker_transition_issue("TEST-1", "close")
        assert "error" in result

    @patch.dict("os.environ", ENV)
    async def test_tracker_patch_issue_tool(self):
        from core.tracker_tools import tracker_patch_issue

        with patch(
            "core.tracker.TrackerClient._request",
            AsyncMock(return_value=ISSUE_RESPONSE),
        ):
            result = await tracker_patch_issue("TEST-1", deadline="2026-06-01", story_points="5")
        assert result["key"] == "TEST-1"

    @patch.dict("os.environ", ENV)
    async def test_tracker_find_issues_tool(self):
        from core.tracker_tools import tracker_find_issues

        with patch(
            "core.tracker.TrackerClient._request",
            AsyncMock(return_value=[ISSUE_RESPONSE]),
        ):
            result = await tracker_find_issues(summary_hint="CI", assignee="shinkarenkorom")

        assert result["count"] == 1
        assert result["issues"][0]["key"] == "TEST-1"

    @patch.dict("os.environ", ENV)
    async def test_tracker_get_queue_meta_tool(self):
        from core.tracker_tools import tracker_get_queue_meta

        meta = {
            "queue_key": "TEST",
            "queue_name": "Test",
            "issue_types": [],
            "priorities": [],
            "local_fields": [],
            "hint": "x",
        }
        with patch(
            "core.tracker.TrackerClient.get_queue_meta",
            AsyncMock(return_value=meta),
        ):
            result = await tracker_get_queue_meta("TEST")
        assert result["queue_key"] == "TEST"


class TestAssigneeHelpers:
    def test_build_find_yql_login(self):
        from core.tracker_tool_helpers import build_find_yql

        yql = build_find_yql(summary_hint="CI", assignee_login="shinkarenkorom")
        assert "Assignee: shinkarenkorom" in yql

    def test_normalize_invalid_yql(self):
        from core.tracker_tool_helpers import normalize_tracker_yql

        assert 'Assignee: "Рома"' in normalize_tracker_yql("assignee = 'Рома'")
        assert normalize_tracker_yql('summary:"MCP"') == 'Summary: "MCP"'

    def test_find_fallback_queries(self):
        from core.tracker_tool_helpers import build_find_fallback_queries

        qs = build_find_fallback_queries(summary_hint="MCP", assignee_login="shinkarenkorom")
        assert "Summary: MCP" in qs
        assert '"MCP"' in qs
        assert "Assignee: shinkarenkorom" in qs
