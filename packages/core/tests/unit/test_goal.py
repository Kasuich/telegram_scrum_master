from __future__ import annotations

import json

from core.goal import (
    GoalItem,
    GoalPlan,
    _finalize_goal_plan,
    _parse_decompose_json,
    _rules_fallback,
    build_goal_plan,
    deserialize_plan,
    serialize_plan,
)
from core.stage_graph import StageId


def test_goal_item_defaults():
    item = GoalItem(stage=StageId.QUERY, payload="hi", intent="ask")
    assert item.entities == {}
    assert item.success_criteria == ""
    assert item.missing_info == []
    assert item.rationale is None


def test_goal_item_full_init():
    item = GoalItem(
        stage=StageId.INTAKE,
        payload="create task",
        intent="create",
        entities={"person": "Alice"},
        success_criteria="task exists",
        missing_info=["priority"],
        rationale="user asked",
    )
    assert item.stage is StageId.INTAKE
    assert item.payload == "create task"
    assert item.intent == "create"
    assert item.entities == {"person": "Alice"}
    assert item.success_criteria == "task exists"
    assert item.missing_info == ["priority"]
    assert item.rationale == "user asked"


def test_goal_item_entities_dict():
    a = GoalItem(stage=StageId.QUERY, payload="a", intent="")
    b = GoalItem(stage=StageId.QUERY, payload="b", intent="")
    a.entities["key"] = "val"
    assert b.entities == {}


def test_goal_plan_defaults():
    plan = GoalPlan()
    assert plan.items == []
    assert plan.is_dialog is False


def test_goal_plan_single_action_stage():
    plan = GoalPlan.single(StageId.QUERY, "hello")
    assert len(plan.items) == 1
    assert plan.is_dialog is False
    assert plan.items[0].stage is StageId.QUERY
    assert plan.items[0].payload == "hello"


def test_goal_plan_single_dialog_stage():
    plan = GoalPlan.single(StageId.DIALOG, "hi")
    assert plan.is_dialog is True
    assert plan.items[0].stage is StageId.DIALOG


def test_goal_plan_dialog():
    plan = GoalPlan.dialog("hi")
    assert len(plan.items) == 1
    assert plan.is_dialog is True


def test_goal_plan_dialog_item_stage():
    plan = GoalPlan.dialog("hi")
    assert plan.items[0].stage is StageId.DIALOG


def test_serialize_plan_basic():
    items = [
        GoalItem(stage=StageId.INTAKE, payload="create", intent="create", entities={"a": "1"}),
        GoalItem(stage=StageId.QUERY, payload="list", intent="list", missing_info=["x"]),
    ]
    plan = GoalPlan(items=items, is_dialog=False)
    data = serialize_plan(plan)
    assert len(data["items"]) == 2
    assert data["is_dialog"] is False
    it0 = data["items"][0]
    assert it0["stage"] == "INTAKE"
    assert it0["payload"] == "create"
    assert it0["intent"] == "create"
    assert it0["entities"] == {"a": "1"}
    assert it0["rationale"] is None
    it1 = data["items"][1]
    assert it1["missing_info"] == ["x"]


def test_serialize_plan_dialog():
    plan = GoalPlan.dialog("hi")
    data = serialize_plan(plan)
    assert data["is_dialog"] is True


def test_serialize_roundtrip():
    items = [
        GoalItem(
            stage=StageId.TRANSITION,
            payload="close X-1",
            intent="close",
            entities={"queue": "X"},
            success_criteria="closed",
            missing_info=["assignee"],
            rationale="user request",
        ),
    ]
    plan = GoalPlan(items=items, is_dialog=False)
    data = serialize_plan(plan)
    restored = deserialize_plan(data)
    assert restored is not None
    assert len(restored.items) == 1
    r = restored.items[0]
    assert r.stage is StageId.TRANSITION
    assert r.payload == "close X-1"
    assert r.intent == "close"
    assert r.entities == {"queue": "X"}
    assert r.success_criteria == "closed"
    assert r.missing_info == ["assignee"]
    assert r.rationale == "user request"


def test_serialize_empty_plan():
    plan = GoalPlan()
    data = serialize_plan(plan)
    assert data["items"] == []
    assert data["is_dialog"] is False


def test_deserialize_none():
    assert deserialize_plan(None) is None


def test_deserialize_empty_dict():
    assert deserialize_plan({}) is None


def test_deserialize_missing_items():
    result = deserialize_plan({"is_dialog": True})
    assert result is not None
    assert result.items == []
    assert result.is_dialog is True


def test_deserialize_invalid_stage():
    data = {
        "items": [{"stage": "INVALID", "payload": "hello", "intent": ""}],
    }
    result = deserialize_plan(data)
    assert result is not None
    assert result.items == []


def test_deserialize_empty_payload():
    data = {
        "items": [{"stage": "QUERY", "payload": "", "intent": ""}],
    }
    result = deserialize_plan(data)
    assert result is not None
    assert result.items == []


def test_deserialize_non_dict_entities():
    data = {
        "items": [{"stage": "QUERY", "payload": "hi", "intent": "", "entities": "not a dict"}],
    }
    result = deserialize_plan(data)
    assert result is not None
    assert len(result.items) == 1
    assert result.items[0].entities == {}


def test_deserialize_non_list_missing_info():
    data = {
        "items": [{"stage": "QUERY", "payload": "hi", "intent": "", "missing_info": "not a list"}],
    }
    result = deserialize_plan(data)
    assert result is not None
    assert len(result.items) == 1
    assert result.items[0].missing_info == []


def test_deserialize_none_rationale():
    data = {
        "items": [{"stage": "QUERY", "payload": "hi", "intent": "", "rationale": None}],
    }
    result = deserialize_plan(data)
    assert result is not None
    assert result.items[0].rationale is None


def test_deserialize_full_roundtrip():
    items = [
        GoalItem(
            stage=StageId.INTAKE,
            payload="create",
            intent="create",
            entities={"p": "v"},
            success_criteria="ok",
            missing_info=["x"],
            rationale="r",
        ),
        GoalItem(stage=StageId.QUERY, payload="list", intent="list"),
    ]
    plan = GoalPlan(items=items, is_dialog=False)
    restored = deserialize_plan(serialize_plan(plan))
    assert restored is not None
    assert len(restored.items) == 2
    assert restored.items[0].stage is StageId.INTAKE
    assert restored.items[0].entities == {"p": "v"}
    assert restored.items[0].success_criteria == "ok"
    assert restored.items[0].missing_info == ["x"]
    assert restored.items[0].rationale == "r"
    assert restored.items[1].stage is StageId.QUERY
    assert restored.is_dialog is False


def test_parse_valid_json():
    raw = json.dumps(
        {
            "goals": [
                {
                    "stage": "INTAKE",
                    "payload": "создай задачу",
                    "intent": "create",
                    "entities": {},
                    "success_criteria": "ok",
                    "missing_info": [],
                }
            ]
        }
    )
    plan = _parse_decompose_json(raw, "создай задачу")
    assert plan is not None
    assert len(plan.items) == 1
    assert plan.items[0].stage is StageId.INTAKE
    assert plan.items[0].payload == "создай задачу"


def test_parse_json_in_code_fence():
    inner = json.dumps({"goals": [{"stage": "QUERY", "payload": "list", "intent": "list"}]})
    raw = f"```json\n{inner}\n```"
    plan = _parse_decompose_json(raw, "list")
    assert plan is not None
    assert plan.items[0].stage is StageId.QUERY


def test_parse_json_in_plain_fence():
    inner = json.dumps({"goals": [{"stage": "QUERY", "payload": "list", "intent": "list"}]})
    raw = f"```\n{inner}\n```"
    plan = _parse_decompose_json(raw, "list")
    assert plan is not None
    assert plan.items[0].stage is StageId.QUERY


def test_parse_empty_string():
    assert _parse_decompose_json("", "msg") is None


def test_parse_no_goals_key():
    assert _parse_decompose_json(json.dumps({"items": []}), "msg") is None


def test_parse_goals_not_list():
    assert _parse_decompose_json(json.dumps({"goals": "not a list"}), "msg") is None


def test_parse_empty_goals():
    assert _parse_decompose_json(json.dumps({"goals": []}), "msg") is None


def test_parse_non_dict_entry():
    raw = json.dumps({"goals": [42]})
    assert _parse_decompose_json(raw, "msg") is None


def test_parse_invalid_stage_skipped():
    raw = json.dumps({"goals": [{"stage": "FAKE", "payload": "hi", "intent": ""}]})
    assert _parse_decompose_json(raw, "hi") is None


def test_parse_max_6_items():
    goals = [{"stage": "INTAKE", "payload": f"t{i}", "intent": ""} for i in range(8)]
    raw = json.dumps({"goals": goals})
    plan = _parse_decompose_json(raw, "msg")
    assert plan is not None
    assert len(plan.items) == 6


def test_parse_payload_fallback_to_message():
    entry = {"stage": "QUERY", "intent": "list"}
    raw = json.dumps({"goals": [entry]})
    plan = _parse_decompose_json(raw, "original message")
    assert plan is not None
    assert plan.items[0].payload == "original message"


def test_parse_empty_payload_fallback():
    entry = {"stage": "QUERY", "payload": "", "intent": "list"}
    raw = json.dumps({"goals": [entry]})
    plan = _parse_decompose_json(raw, "fallback msg")
    assert plan is not None
    assert plan.items[0].payload == "fallback msg"


def test_parse_malformed_json_recovery():
    goals = [{"stage": "QUERY", "payload": "hi", "intent": ""}]
    inner = json.dumps({"goals": goals})
    raw = f"some prefix {inner} some suffix"
    plan = _parse_decompose_json(raw, "hi")
    assert plan is not None
    assert plan.items[0].stage is StageId.QUERY


def test_parse_completely_invalid_json():
    assert _parse_decompose_json("not json at all", "msg") is None


def test_parse_entities_not_dict():
    raw = json.dumps(
        {"goals": [{"stage": "QUERY", "payload": "hi", "intent": "", "entities": "bad"}]}
    )
    plan = _parse_decompose_json(raw, "hi")
    assert plan is not None
    assert plan.items[0].entities == {}


def test_parse_missing_info_not_list():
    raw = json.dumps(
        {"goals": [{"stage": "QUERY", "payload": "hi", "intent": "", "missing_info": "bad"}]}
    )
    plan = _parse_decompose_json(raw, "hi")
    assert plan is not None
    assert plan.items[0].missing_info == []


def test_finalize_dialog_dropped_when_actionable_exists():
    items = [
        GoalItem(stage=StageId.DIALOG, payload="hi", intent=""),
        GoalItem(stage=StageId.INTAKE, payload="create", intent=""),
    ]
    plan = _finalize_goal_plan(items)
    assert plan.is_dialog is False
    assert all(i.stage != StageId.DIALOG for i in plan.items)


def test_finalize_dialog_only():
    items = [GoalItem(stage=StageId.DIALOG, payload="hi", intent="")]
    plan = _finalize_goal_plan(items)
    assert plan.is_dialog is True
    assert len(plan.items) == 1
    assert plan.items[0].stage is StageId.DIALOG


def test_finalize_multiple_dialog_no_actionable():
    items = [
        GoalItem(stage=StageId.DIALOG, payload="hi", intent=""),
        GoalItem(stage=StageId.DIALOG, payload="bye", intent=""),
    ]
    plan = _finalize_goal_plan(items)
    assert plan.is_dialog is False
    assert len(plan.items) == 2


def test_finalize_capped_at_6():
    items = [GoalItem(stage=StageId.INTAKE, payload=f"t{i}", intent="") for i in range(7)]
    plan = _finalize_goal_plan(items)
    assert len(plan.items) == 6


def test_rules_fallback_create_message():
    plan = _rules_fallback("создай задачу для логина")
    assert len(plan.items) == 1
    assert plan.items[0].stage is StageId.INTAKE


def test_rules_fallback_close_message():
    plan = _rules_fallback("закрой задачу DARKHORSE-5")
    assert len(plan.items) == 1
    assert plan.items[0].stage is StageId.TRANSITION


def test_rules_fallback_query_message():
    plan = _rules_fallback("сколько задач открыто?")
    assert len(plan.items) == 1
    assert plan.items[0].stage is StageId.QUERY


def test_rules_fallback_unknown_message():
    plan = _rules_fallback("абракадабра непонятно")
    assert len(plan.items) == 1
    assert plan.items[0].stage is StageId.QUERY


def test_rules_fallback_returns_single_item():
    plan = _rules_fallback("закрой задачу X")
    assert len(plan.items) == 1


async def test_build_goal_plan_no_llm():
    plan = await build_goal_plan("создай задачу", use_llm=False)
    assert plan is not None
    assert len(plan.items) >= 1


async def test_build_goal_plan_no_llm_deterministic():
    msg = "закрой задачу DARKHORSE-1"
    a = await build_goal_plan(msg, use_llm=False)
    b = await build_goal_plan(msg, use_llm=False)
    assert a.items[0].stage is b.items[0].stage
    assert a.items[0].payload == b.items[0].payload
