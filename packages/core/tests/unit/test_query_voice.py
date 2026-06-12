"""Tests for QUERY-voice: read-questions return LLM text, not action reports."""

from __future__ import annotations

from core.react import (
    _READ_VOICE_STAGES,
    _action_only_final_reply,
    _build_action_report,
    _format_read_tool_reply,
)
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
    # Empty LLM text on a read → graceful human fallback, never a tool name.
    assert "Данные поднял" in reply
    assert "tracker_board_snapshot" not in reply


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
    steps = [
        {
            "kind": "tool_result",
            "tool_name": "tracker_close_issue",
            "result": {"issue": {"key": "D-1"}},
        }
    ]
    llm_text = "Закрыто!"
    reply = _action_only_final_reply(steps, llm_text, had_tool=True, stage_id=StageId.TRANSITION)
    assert reply != llm_text


def test_freeform_read_only_turn_prefers_llm_text_despite_misstage():
    # "что взять в работу" matches the TRANSITION marker "в работу", so the
    # rules-router mis-stages a pure read+advice turn. A freeform agent must
    # still return its real answer, not the terse "Нашёл N задач" report.
    steps = [{"kind": "tool_result", "tool_name": "GetIssues", "result": {"issues": [{}] * 5}}]
    llm_text = "Вот твои 5 задач: ... Рекомендую начать с DARKHORSE-289 — дедлайн завтра."
    report = _build_action_report(steps)
    reply = _action_only_final_reply(
        steps, llm_text, had_tool=True, stage_id=StageId.TRANSITION, freeform=True
    )
    assert reply == llm_text
    assert reply != report


def test_freeform_write_turn_still_uses_action_report():
    # A freeform turn that actually changed the board keeps the exact report.
    steps = [{"kind": "tool_result", "tool_name": "tracker_create_issue", "result": {"key": "D-1"}}]
    reply = _action_only_final_reply(
        steps, "Готово!", had_tool=True, stage_id=StageId.TRANSITION, freeform=True
    )
    assert reply != "Готово!"
    assert "D-1" in reply


def test_non_freeform_read_only_misstage_unchanged():
    # Without freeform the historical behaviour is preserved: a TRANSITION-staged
    # read turn returns the deterministic report regardless of llm_text.
    steps = [{"kind": "tool_result", "tool_name": "GetIssues", "result": {"issues": [{}] * 5}}]
    reply = _action_only_final_reply(
        steps, "full answer", had_tool=True, stage_id=StageId.TRANSITION, freeform=False
    )
    assert reply != "full answer"


def test_action_only_final_reply_no_tool_no_stage():
    reply = _action_only_final_reply([], "", had_tool=False, stage_id=None)
    assert reply == "Пока ничего не сделал."


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
        {
            "kind": "tool_result",
            "tool_name": "tracker_board_snapshot",
            "result": {"total": 3, "by_assignee_sp": {"Коля": 8}},
        }
    ]
    llm_text = "На доске 3 задачи, у Коли 8 SP."
    report = _build_action_report(steps)
    reply = _action_only_final_reply(steps, llm_text, had_tool=True, stage_id=StageId.QUERY)
    assert reply == llm_text
    assert reply != report


def test_get_issues_reply_is_rendered_without_service_text():
    reply = _format_read_tool_reply(
        "GetIssues",
        {
            "issues": [
                {
                    "key": "DARKHORSE-272",
                    "summary": "Проверить файлы",
                    "status": {"display": "В работе"},
                    "assignee": {"display": "Roman Shinkarenko"},
                }
            ]
        },
    )
    assert reply == "- DARKHORSE-272 «Проверить файлы» (В работе, Roman Shinkarenko)"


def test_internal_goal_message_is_filtered():
    reply = _action_only_final_reply(
        [{"kind": "tool_result", "tool_name": "GetIssues", "result": {"issues": []}}],
        "Цель запроса достигнута. Нет необходимости в дополнительных действиях.",
        had_tool=True,
        stage_id=StageId.QUERY,
    )
    assert "Цель запроса" not in reply
    assert "DARKHORSE" not in reply
