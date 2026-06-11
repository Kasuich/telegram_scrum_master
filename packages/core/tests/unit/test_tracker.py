"""
Unit tests for TrackerClient and tracker_tools.
All HTTP calls are mocked — no real Tracker access needed.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from core.invocation import (
    InvocationContext,
    reset_current_invocation_context,
    set_current_invocation_context,
)
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
SPRINT_RESPONSE = {
    "self": "https://api.tracker.yandex.net/v3/sprints/44",
    "id": 44,
    "name": "Sprint 1",
    "board": {"id": "3", "display": "Testing"},
    "status": "draft",
    "archived": False,
    "startDate": "2026-06-10",
    "endDate": "2026-06-24",
}
BOARD_RESPONSE = {"id": 3, "name": "Testing"}


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


@contextmanager
def _actor(role: str, *, board_id: str = "3"):
    token = set_current_invocation_context(
        InvocationContext(
            channel="telegram",
            actor_role=role,
            actor_default_board_id=board_id,
            actor_settings={"default_board_name": "Testing"},
        )
    )
    try:
        yield
    finally:
        reset_current_invocation_context(token)


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
    async def test_missing_token_fails_on_request_not_config_load(self):
        c = TrackerClient(token="", org_id="org", org_type="360", base_url=_BASE)
        with pytest.raises(TrackerError, match="TRACKER_TOKEN"):
            await c.get_issue("TEST-1")

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


class TestCreateSprint:
    async def test_posts_sprint_body(self):
        c = _client()
        with _patch_request(_ok(SPRINT_RESPONSE, status=201)) as mock_req:
            result = await c.create_sprint(
                name="Sprint 1",
                board_id="3",
                start_date="2026-06-10",
                end_date="2026-06-24",
            )
        method, url = mock_req.call_args[0]
        body = mock_req.call_args[1]["json"]
        assert method == "POST"
        assert url.endswith("/sprints")
        assert body == {
            "name": "Sprint 1",
            "board": {"id": "3"},
            "startDate": "2026-06-10",
            "endDate": "2026-06-24",
        }
        assert result["id"] == 44

    async def test_lists_boards(self):
        c = _client()
        with _patch_request(_ok([BOARD_RESPONSE])) as mock_req:
            result = await c.list_boards()
        method, url = mock_req.call_args[0]
        assert method == "GET"
        assert url.endswith("/boards")
        assert result[0]["name"] == "Testing"

    async def test_get_board(self):
        c = _client()
        with _patch_request(_ok(BOARD_RESPONSE)) as mock_req:
            result = await c.get_board("3")
        method, url = mock_req.call_args[0]
        assert method == "GET"
        assert url.endswith("/boards/3")
        assert result["name"] == "Testing"

    async def test_lists_board_sprints(self):
        c = _client()
        with _patch_request(_ok([SPRINT_RESPONSE])) as mock_req:
            result = await c.list_sprints("3")
        method, url = mock_req.call_args[0]
        assert method == "GET"
        assert url.endswith("/boards/3/sprints")
        assert result[0]["id"] == 44

    async def test_patches_sprint(self):
        c = _client()
        with _patch_request(_ok({**SPRINT_RESPONSE, "archived": True})) as mock_req:
            result = await c.patch_sprint("44", {"archived": True})
        method, url = mock_req.call_args[0]
        assert method == "PATCH"
        assert url.endswith("/sprints/44")
        assert mock_req.call_args[1]["json"] == {"archived": True}
        assert result["archived"] is True

    async def test_open_and_close_sprint_patch_archived(self):
        c = _client()
        with _patch_request(
            side_effect=[_ok(SPRINT_RESPONSE), _ok({**SPRINT_RESPONSE, "archived": True})]
        ) as mock_req:
            await c.open_sprint("44")
            await c.close_sprint("44")
        assert mock_req.call_args_list[0][1]["json"] == {"archived": False}
        assert mock_req.call_args_list[1][1]["json"] == {"archived": True}

    async def test_add_issue_to_sprint_preserves_existing(self):
        c = _client()
        issue_with_sprint = {**ISSUE_RESPONSE, "sprint": [{"id": "11", "display": "Old"}]}
        with _patch_request(side_effect=[_ok(issue_with_sprint), _ok(ISSUE_RESPONSE)]) as mock_req:
            result = await c.add_issue_to_sprint("TEST-1", "44")
        body = mock_req.call_args_list[1][1]["json"]
        assert body == {"sprint": [{"id": "11"}, {"id": "44"}]}
        assert result["key"] == "TEST-1"

    async def test_add_issue_to_sprint_can_replace_existing(self):
        c = _client()
        with _patch_request(_ok(ISSUE_RESPONSE)) as mock_req:
            await c.add_issue_to_sprint("TEST-1", "44", preserve_existing=False)
        body = mock_req.call_args[1]["json"]
        assert body == {"sprint": [{"id": "44"}]}

    async def test_move_issue_to_sprint_replaces_only_old_sprint(self):
        c = _client()
        issue = {
            **ISSUE_RESPONSE,
            "sprint": [
                {"id": "11", "display": "Old"},
                {"id": "22", "display": "Other"},
            ],
        }
        with _patch_request(side_effect=[_ok(issue), _ok(ISSUE_RESPONSE)]) as mock_req:
            result = await c.move_issue_to_sprint("TEST-1", "11", "44")
        body = mock_req.call_args_list[1][1]["json"]
        assert body == {"sprint": [{"id": "22"}, {"id": "44"}]}
        assert result["key"] == "TEST-1"


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

    async def test_executes_matching_target_status_display(self):
        c = _client()
        transitions = [{"id": "start", "display": "Взять в работу", "to": {"display": "В работе"}}]
        with _patch_request(side_effect=[_ok(transitions), _ok({"status": "inProgress"})]):
            result = await c.transition_issue("TEST-1", "в работе")
        assert result == {"status": "inProgress"}

    async def test_executes_close_by_target_status_display(self):
        c = _client()
        transitions = [{"id": "finish", "display": "Завершить", "to": {"display": "Закрыто"}}]
        with _patch_request(side_effect=[_ok(transitions), _ok({"status": "closed"})]) as mock_req:
            await c.transition_issue("TEST-1", "closed", resolution="fixed")
        assert mock_req.call_args_list[1][0][1].endswith(
            "/issues/TEST-1/transitions/finish/_execute"
        )

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
    async def test_tracker_create_issue_reports_duplicate_without_merge(self):
        from core.issue_dedup import DedupResolution
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
            with patch(
                "core.tracker_tools.resolve_planned_issues_dedup",
                new_callable=AsyncMock,
                return_value=(
                    [
                        DedupResolution(
                            planned_id="0",
                            action="merge",
                            duplicate_key="TEST-99",
                            comment="new context",
                            target_status="inProgress",
                        )
                    ],
                    {"TEST-99": existing},
                ),
            ):
                result = await tracker_create_issue(
                    "Fix login bug",
                    queue="TEST",
                    description="Details from meeting",
                )

        assert result["key"] == "TEST-99"
        assert result.get("duplicate_found") is True
        assert result.get("skipped_create") is True
        assert result.get("planned_create", {}).get("description") == "Details from meeting"
        assert result.get("suggested_updates", {}).get("comment") == "new context"
        client.create_issue.assert_not_called()
        client.patch_issue.assert_not_called()
        client.comment_issue.assert_not_called()

    @patch.dict("os.environ", ENV)
    async def test_tracker_create_issue_allow_duplicate_bypasses_dedup(self):
        from core.tracker_tools import tracker_create_issue

        with patch("core.tracker_tools.TrackerClient") as mock_cls:
            client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = client
            client.create_issue.return_value = ISSUE_RESPONSE
            with patch(
                "core.tracker_tools.resolve_planned_issues_dedup",
                new_callable=AsyncMock,
            ) as mock_dedup:
                result = await tracker_create_issue(
                    "Fix login bug", queue="TEST", allow_duplicate=True
                )

        assert result["key"] == "TEST-1"
        assert result.get("skipped_duplicate") is None
        mock_dedup.assert_not_called()
        client.create_issue.assert_called_once()

    @patch.dict("os.environ", ENV)
    async def test_tracker_create_sprint_tool(self):
        from core.tracker_tools import tracker_create_sprint

        with patch(
            "core.tracker.TrackerClient.create_sprint",
            AsyncMock(return_value=SPRINT_RESPONSE),
        ) as mock_create:
            with _actor("lead"):
                result = await tracker_create_sprint(
                    "Sprint 1",
                    board_id="3",
                    start_date="2026-06-10",
                    end_date="2026-06-24",
                )

        mock_create.assert_awaited_once_with(
            name="Sprint 1",
            board_id="3",
            start_date="2026-06-10",
            end_date="2026-06-24",
        )
        assert result["id"] == 44
        assert result["board_id"] == "3"
        assert result["start_date"] == "2026-06-10"

    @patch.dict("os.environ", ENV)
    async def test_tracker_create_sprint_tool_resolves_board_name(self):
        from core.tracker_tools import tracker_create_sprint

        with patch("core.tracker_tools.TrackerClient") as mock_cls:
            client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = client
            client.list_boards.return_value = [BOARD_RESPONSE]
            client.create_sprint.return_value = SPRINT_RESPONSE

            with _actor("lead"):
                result = await tracker_create_sprint(
                    "Sprint 1",
                    board_name="Testing",
                    start_date="2026-06-10",
                    end_date="2026-06-24",
                )

        client.create_sprint.assert_awaited_once_with(
            name="Sprint 1",
            board_id="3",
            start_date="2026-06-10",
            end_date="2026-06-24",
        )
        assert result["board_id"] == "3"
        assert result["board"] == "Testing"

    @patch.dict("os.environ", ENV)
    async def test_tracker_add_issues_to_sprint_by_name(self):
        from core.tracker_tools import tracker_add_issues_to_sprint

        with patch("core.tracker_tools.TrackerClient") as mock_cls:
            client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = client
            client.list_boards.return_value = [BOARD_RESPONSE]
            client.list_sprints.return_value = [SPRINT_RESPONSE]
            client.add_issue_to_sprint.side_effect = [
                {**ISSUE_RESPONSE, "key": "TEST-1"},
                {**ISSUE_RESPONSE, "key": "TEST-2"},
            ]

            result = await tracker_add_issues_to_sprint(
                "TEST-1, TEST-2",
                sprint_name="Sprint 1",
                board_name="Testing",
            )

        assert result["sprint_id"] == "44"
        assert result["updated_count"] == 2
        assert result["error_count"] == 0
        client.add_issue_to_sprint.assert_any_await("TEST-1", "44", preserve_existing=True)
        client.add_issue_to_sprint.assert_any_await("TEST-2", "44", preserve_existing=True)

    @patch.dict("os.environ", ENV)
    async def test_tracker_create_sprint_requires_lead_or_admin(self):
        from core.tracker_tools import tracker_create_sprint

        with _actor("user"):
            result = await tracker_create_sprint(
                "Sprint 1",
                board_id="3",
                start_date="2026-06-10",
                end_date="2026-06-24",
            )

        assert result["error"] == "Sprint creation is allowed only for team lead/admin"
        assert result["actor_role"] == "user"

    @patch.dict("os.environ", ENV)
    async def test_tracker_create_sprint_uses_default_board_from_context(self):
        from core.tracker_tools import tracker_create_sprint

        with patch("core.tracker_tools.TrackerClient") as mock_cls:
            client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = client
            client.create_sprint.return_value = SPRINT_RESPONSE

            with _actor("admin", board_id="3"):
                result = await tracker_create_sprint(
                    "Sprint 1",
                    start_date="2026-06-10",
                    end_date="2026-06-24",
                )

        client.create_sprint.assert_awaited_once_with(
            name="Sprint 1",
            board_id="3",
            start_date="2026-06-10",
            end_date="2026-06-24",
        )
        assert result["board_id"] == "3"

    @patch.dict("os.environ", ENV)
    async def test_tracker_create_issue_blocks_epic_for_user(self):
        from core.tracker_tools import tracker_create_issue

        with _actor("user"):
            result = await tracker_create_issue("Epic title", issue_type="epic")

        assert result["error"] == "Epic creation is allowed only for team lead/admin"

    @patch.dict("os.environ", ENV)
    async def test_tracker_create_epic_wraps_create_issue(self):
        from core.tracker_tools import tracker_create_epic

        with patch(
            "core.tracker_tools.tracker_create_issue",
            AsyncMock(return_value=ISSUE_RESPONSE),
        ) as mock_create:
            with _actor("lead"):
                result = await tracker_create_epic("Epic title", description="Scope")

        mock_create.assert_awaited_once()
        assert mock_create.call_args.kwargs["issue_type"] == "epic"
        assert result["key"] == "TEST-1"

    @patch.dict("os.environ", ENV)
    async def test_tracker_open_and_close_sprint_tools(self):
        from core.tracker_tools import tracker_close_sprint, tracker_open_sprint

        with patch("core.tracker_tools.TrackerClient") as mock_cls:
            client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = client
            client.open_sprint.return_value = SPRINT_RESPONSE
            client.close_sprint.return_value = {**SPRINT_RESPONSE, "archived": True}

            with _actor("lead"):
                opened = await tracker_open_sprint(sprint_id="44")
                closed = await tracker_close_sprint(sprint_id="44")

        client.open_sprint.assert_awaited_once_with("44")
        client.close_sprint.assert_awaited_once_with("44")
        assert opened["opened"] is True
        assert closed["closed"] is True
        assert closed["archived"] is True

    @patch.dict("os.environ", ENV)
    async def test_tracker_rollover_sprint_moves_non_closed_without_transitions(self):
        from core.tracker_tools import tracker_rollover_sprint

        old_sprint = {
            **SPRINT_RESPONSE,
            "id": 44,
            "name": "Sprint 9",
            "startDate": "2026-06-01",
            "endDate": "2026-06-14",
        }
        new_sprint = {
            **SPRINT_RESPONSE,
            "id": 45,
            "name": "Sprint 10",
            "startDate": "2026-06-15",
            "endDate": "2026-06-28",
        }
        open_issue = {
            **ISSUE_RESPONSE,
            "key": "TEST-1",
            "status": {"key": "open", "display": "Open"},
            "sprint": [{"id": "44"}],
        }
        cancelled_issue = {
            **ISSUE_RESPONSE,
            "key": "TEST-2",
            "status": {"key": "cancelled", "display": "Cancelled"},
            "sprint": [{"id": "44"}],
        }
        closed_issue = {
            **ISSUE_RESPONSE,
            "key": "TEST-3",
            "status": {"key": "closed", "display": "Closed"},
            "sprint": [{"id": "44"}],
        }

        with patch("core.tracker_tools.TrackerClient") as mock_cls:
            client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = client
            client.get_sprint.return_value = old_sprint
            client.create_sprint.return_value = new_sprint
            client.search_all_issues.return_value = [open_issue, cancelled_issue, closed_issue]
            client.move_issue_to_sprint.side_effect = [open_issue, cancelled_issue]
            client.close_sprint.return_value = {**old_sprint, "archived": True}

            with _actor("lead"):
                result = await tracker_rollover_sprint(sprint_id="44", board_id="3")

        client.create_sprint.assert_awaited_once_with(
            name="Sprint 10",
            board_id="3",
            start_date="2026-06-15",
            end_date="2026-06-28",
        )
        client.move_issue_to_sprint.assert_any_await("TEST-1", "44", "45")
        client.move_issue_to_sprint.assert_any_await("TEST-2", "44", "45")
        assert client.move_issue_to_sprint.await_count == 2
        client.transition_issue.assert_not_called()
        assert result["moved_count"] == 2
        assert result["statuses_preserved"] is True

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
    async def test_tracker_move_issues_to_in_progress_tool(self):
        from core.tracker_tools import tracker_move_issues_to_in_progress

        with patch("core.tracker_tools.TrackerClient") as mock_cls:
            client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = client
            client.transition_issue.return_value = {"status": "inProgress"}
            client.get_issue.side_effect = [
                {**ISSUE_RESPONSE, "key": "TEST-1"},
                {**ISSUE_RESPONSE, "key": "TEST-2"},
            ]

            result = await tracker_move_issues_to_in_progress("TEST-1, TEST-2")

        assert result["updated_count"] == 2
        assert result["error_count"] == 0
        client.transition_issue.assert_any_await("TEST-1", "in_progress", comment=None)
        client.transition_issue.assert_any_await("TEST-2", "in_progress", comment=None)

    @patch.dict("os.environ", ENV)
    async def test_tracker_close_issues_tool(self):
        from core.tracker_tools import tracker_close_issues

        with patch("core.tracker_tools.TrackerClient") as mock_cls:
            client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = client
            client.transition_issue.return_value = {"status": "closed"}
            client.get_issue.return_value = {**ISSUE_RESPONSE, "key": "TEST-1"}

            result = await tracker_close_issues("TEST-1", resolution="fixed")

        assert result["closed_count"] == 1
        assert result["error_count"] == 0
        client.transition_issue.assert_awaited_once_with(
            "TEST-1",
            "closed",
            resolution="fixed",
            comment=None,
        )

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
    async def test_tracker_find_issues_query_key_fetches_exact_issue(self):
        from core.tracker_tools import tracker_find_issues

        with patch("core.tracker_tools.TrackerClient") as mock_cls:
            client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = client
            client.get_issue.return_value = {**ISSUE_RESPONSE, "key": "DARKHORSE-171"}

            result = await tracker_find_issues(query="key:DARKHORSE-171")

        assert result["count"] == 1
        assert result["query_used"] == "key:DARKHORSE-171"
        assert result["issues"][0]["key"] == "DARKHORSE-171"
        client.get_issue.assert_awaited_once_with("DARKHORSE-171")
        client.search_issues.assert_not_called()

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
