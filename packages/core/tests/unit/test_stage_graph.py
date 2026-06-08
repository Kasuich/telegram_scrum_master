"""Tests for the stage graph (pure, sync, no LLM)."""

from __future__ import annotations

from core.stage_graph import STAGES, StageId, ToolCallSpec, get_stage


def _result(tool_name, result, **kw):
    return {"kind": "tool_result", "tool_name": tool_name, "result": result, **kw}


def _call(tool_name, **kw):
    return {"kind": "tool_call", "tool_name": tool_name, **kw}


# ---------------------------------------------------------------------------
# get_stage
# ---------------------------------------------------------------------------


def test_get_stage_by_enum_str_and_none():
    assert get_stage(StageId.QUERY).id is StageId.QUERY
    assert get_stage("BOARD").id is StageId.BOARD
    assert get_stage(None) is None
    assert get_stage("NOPE") is None


# ---------------------------------------------------------------------------
# STATUS stage
# ---------------------------------------------------------------------------


def test_status_blocks_backlog_plan():
    stage = STAGES[StageId.STATUS]
    d = stage.check_tool("backlog_plan", {}, [])
    assert not d.allow
    assert "backlog_plan" in d.reason


def test_status_call_agent_requires_find_and_summarizer_target():
    stage = STAGES[StageId.STATUS]
    # wrong target
    d = stage.check_tool("call_agent", {"target_agent": "other"}, [])
    assert not d.allow and "meeting_summarizer" in d.reason
    # right target but no find yet
    d = stage.check_tool("call_agent", {"target_agent": "meeting_summarizer"}, [])
    assert not d.allow
    # after a successful find
    steps = [_result("tracker_find_issues", {"count": 1, "issues": [{"key": "T-1"}]})]
    d = stage.check_tool("call_agent", {"target_agent": "meeting_summarizer"}, steps)
    assert d.allow


def test_status_comment_requires_summarizer_first():
    stage = STAGES[StageId.STATUS]
    steps = [_result("tracker_find_issues", {"count": 1, "issues": [{"key": "T-1"}]})]
    d = stage.check_tool("tracker_comment_issue", {"issue_key": "T-1", "text": "x"}, steps)
    assert not d.allow and "meeting_summarizer" in d.reason
    steps.append(
        _result("call_agent", "**Статус**", tool_args={"target_agent": "meeting_summarizer"})
    )
    d = stage.check_tool("tracker_comment_issue", {"issue_key": "T-1", "text": "x"}, steps)
    assert d.allow


def test_status_terminal_on_comment():
    stage = STAGES[StageId.STATUS]
    assert not stage.is_terminal([])
    steps = [_result("tracker_comment_issue", {"issue_key": "T-1", "text": "x"})]
    assert stage.is_terminal(steps)


# ---------------------------------------------------------------------------
# BOARD stage
# ---------------------------------------------------------------------------


def test_board_blocks_single_create():
    stage = STAGES[StageId.BOARD]
    d = stage.check_tool("tracker_create_issue", {"summary": "x"}, [])
    assert not d.allow and "backlog_plan" in d.reason


def test_board_allows_plan_and_apply():
    stage = STAGES[StageId.BOARD]
    assert stage.check_tool("backlog_plan", {"text": "..."}, []).allow
    assert stage.check_tool("tracker_apply_backlog_plan", {"plan_json": ""}, []).allow


def test_board_forced_next_applies_after_plan():
    stage = STAGES[StageId.BOARD]
    # no plan yet
    assert stage.next_forced_step([]) is None
    steps = [_result("backlog_plan", {"plan": {"x": 1}, "tasks_count": 3})]
    forced = stage.next_forced_step(steps)
    assert isinstance(forced, ToolCallSpec)
    assert forced.tool_name == "tracker_apply_backlog_plan"
    assert forced.tool_args == {"plan_json": ""}
    # once apply is seen, no more forcing
    steps.append(_call("tracker_apply_backlog_plan", tool_args={"plan_json": ""}))
    assert stage.next_forced_step(steps) is None


def test_board_terminal_on_apply_with_created():
    stage = STAGES[StageId.BOARD]
    assert not stage.is_terminal([_result("tracker_apply_backlog_plan", {"created_count": 0})])
    assert stage.is_terminal([_result("tracker_apply_backlog_plan", {"created_count": 5})])


# ---------------------------------------------------------------------------
# CREATE / INTAKE stage
# ---------------------------------------------------------------------------


def test_intake_blocks_close_after_create():
    stage = STAGES[StageId.INTAKE]
    steps = [_result("tracker_create_issue", {"key": "T-9"})]
    d = stage.check_tool("tracker_close_issue", {"issue_key": "T-9"}, steps)
    assert not d.allow and "Запрещено закрывать" in d.reason


def test_intake_blocks_second_create_but_allows_subtask():
    stage = STAGES[StageId.INTAKE]
    steps = [_result("tracker_create_issue", {"key": "T-9"})]
    d = stage.check_tool("tracker_create_issue", {"summary": "y"}, steps)
    assert not d.allow
    # subtask with parent is allowed
    d = stage.check_tool("tracker_create_issue", {"summary": "y", "parent": "T-9"}, steps)
    assert d.allow


def test_intake_terminal_on_created_key():
    stage = STAGES[StageId.INTAKE]
    assert not stage.is_terminal([])
    assert stage.is_terminal([_result("tracker_create_issue", {"key": "T-1"})])


def test_intake_allows_and_terminates_on_create_sprint():
    stage = STAGES[StageId.INTAKE]
    assert stage.check_tool(
        "tracker_create_sprint",
        {
            "name": "Sprint 1",
            "board_id": "3",
            "start_date": "2026-06-10",
            "end_date": "2026-06-24",
        },
        [],
    ).allow
    assert stage.is_terminal([_result("tracker_create_sprint", {"id": 44})])


# ---------------------------------------------------------------------------
# TRANSITION stage
# ---------------------------------------------------------------------------


def test_transition_allows_transition_blocks_create():
    stage = STAGES[StageId.TRANSITION]
    assert stage.check_tool("tracker_transition_issue", {"issue_key": "T-1"}, []).allow
    assert stage.check_tool("tracker_move_issues_to_in_progress", {"issue_keys": "T-1"}, []).allow
    assert stage.check_tool("tracker_close_issue", {"issue_key": "T-1"}, []).allow
    assert stage.check_tool("tracker_close_issues", {"issue_keys": "T-1,T-2"}, []).allow
    assert not stage.check_tool("tracker_create_issue", {"summary": "x"}, []).allow


def test_transition_terminal():
    stage = STAGES[StageId.TRANSITION]
    assert stage.is_terminal([_result("tracker_close_issue", {"issue_key": "T-1"})])
    assert stage.is_terminal([_result("tracker_close_issues", {"closed_count": 2})])
    assert stage.is_terminal([_result("tracker_transition_issue", {"issue_key": "T-1"})])
    assert stage.is_terminal([_result("tracker_move_issues_to_in_progress", {"updated_count": 1})])


# ---------------------------------------------------------------------------
# QUERY stage (read-only)
# ---------------------------------------------------------------------------


def test_query_allows_reads_blocks_writes():
    stage = STAGES[StageId.QUERY]
    assert stage.check_tool("tracker_board_snapshot", {}, []).allow
    assert stage.check_tool("tracker_find_issues", {"assignee": "Коля"}, []).allow
    for write in ("tracker_create_issue", "tracker_patch_issue", "tracker_close_issue"):
        d = stage.check_tool(write, {}, [])
        assert not d.allow and "QUERY" in d.reason


def test_query_terminal_on_first_read():
    stage = STAGES[StageId.QUERY]
    assert not stage.is_terminal([])
    assert stage.is_terminal([_result("tracker_board_snapshot", {"total": 10})])
    assert stage.is_terminal([_result("tracker_find_issues", {"count": 0, "issues": []})])


# ---------------------------------------------------------------------------
# PROACTIVE / HYGIENE / REORG terminal = never (bounded by max_iterations)
# ---------------------------------------------------------------------------


def test_open_ended_stages_never_self_terminate():
    for sid in (StageId.REORG, StageId.PROACTIVE, StageId.HYGIENE):
        stage = STAGES[sid]
        assert not stage.is_terminal([_result("tracker_patch_issue", {"key": "T-1"})])


def test_reorg_allows_add_issues_to_sprint():
    stage = STAGES[StageId.REORG]
    assert stage.check_tool(
        "tracker_add_issues_to_sprint",
        {"issue_keys": "T-1,T-2", "sprint_name": "Sprint 1", "board_name": "Board"},
        [],
    ).allow


def test_proactive_allows_comment_and_snapshot():
    stage = STAGES[StageId.PROACTIVE]
    assert stage.check_tool("tracker_board_snapshot", {}, []).allow
    assert stage.check_tool("tracker_comment_issue", {"issue_key": "T-1", "text": "x"}, []).allow
