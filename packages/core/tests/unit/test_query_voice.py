"""Tests for QUERY-voice: read-questions return LLM text, not action reports."""
from __future__ import annotations

from core.react import _action_only_final_reply, _READ_VOICE_STAGES, _build_action_report
from core.stage_graph import StageId


def test_action_only_final_reply_query_prefers_llm_text():
    steps = [{"kind": "tool_result", "tool_name": "tracker_board_snapshot", "result": {"total": 5}}]
    llm_text = "У Коли 8 story points."
    reply = _action_only_final_reply(steps, llm_text, had_tool=True, stage_id=StageId.QUERY)
    assert reply == llm_text
    assert "выполнено" not in reply


def test_action_only_final_reply_query_empty_llm_falls_back():
    steps = [{"kind": "tool_result", "tool_name": "tracker_board_snapshot", "result": {"total": 5}}]
    reply = _action_only_final_reply(steps, "", had_tool=True, stage_id=StageId.QUERY)
    assert "tracker_board_snapshot" in reply or "выполнено" in reply


def test_action_only_final_reply_intake_uses_action_report():
    steps = [{"kind": "tool_result", "tool_name": "tracker_create_issue", "result": {"key": "D-1"}}]
    llm_text = "Задача создана!"
    reply = _action_only_final_reply(steps, llm_text, had_tool=True, stage_id=StageId.INTAKE)
    assert reply != llm_text
    assert "D-1" in reply


def test_action_only_final_reply_no_stage_uses_action_report():
    steps = [{"kind": "tool_result", "tool_name": "tracker_create_issue", "result": {"key": "D-1"}}]
    llm_text = "Something"
    reply = _action_only_final_reply(steps, llm_text, had_tool=True, stage_id=None)
    assert reply != llm_text


def test_action_only_final_reply_transition_uses_action_report():
    steps = [{"kind": "tool_result", "tool_name": "tracker_close_issue", "result": {"issue": {"key": "D-1"}}}]
    llm_text = "Закрыто!"
    reply = _action_only_final_reply(steps, llm_text, had_tool=True, stage_id=StageId.TRANSITION)
    assert reply != llm_text


def test_action_only_final_reply_no_tool_no_stage():
    reply = _action_only_final_reply([], "", had_tool=False, stage_id=None)
    assert reply == "Действия не выполнены."


def test_action_only_final_reply_no_tool_query_stage():
    reply = _action_only_final_reply([], "some text", had_tool=False, stage_id=StageId.QUERY)
    assert reply == "some text"


def test_read_voice_stages_only_contains_query():
    assert StageId.QUERY in _READ_VOICE_STAGES
    assert StageId.INTAKE not in _READ_VOICE_STAGES
    assert StageId.STATUS not in _READ_VOICE_STAGES
    assert StageId.TRANSITION not in _READ_VOICE_STAGES
    assert StageId.REORG not in _READ_VOICE_STAGES
    assert StageId.PROACTIVE not in _READ_VOICE_STAGES
    assert StageId.HYGIENE not in _READ_VOICE_STAGES


def test_query_voice_scenario_not_action_report():
    steps = [
        {"kind": "tool_result", "tool_name": "tracker_board_snapshot", "result": {"total": 3, "by_assignee_sp": {"Коля": 8}}}
    ]
    llm_text = "На доске 3 задачи, у Коли 8 SP."
    report = _build_action_report(steps)
    reply = _action_only_final_reply(steps, llm_text, had_tool=True, stage_id=StageId.QUERY)
    assert reply == llm_text
    assert reply != report
