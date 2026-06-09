from __future__ import annotations

from core.goal import GoalItem, GoalPlan
from core.stage_graph import StageId


def test_missing_info_collected_from_single_goal():
    item = GoalItem(
        stage=StageId.INTAKE,
        payload="create task",
        intent="create",
        missing_info=["queue name"],
    )
    plan = GoalPlan(items=[item])
    collected = [info for g in plan.items for info in g.missing_info]
    assert collected == ["queue name"]


def test_missing_info_collected_from_multiple_goals():
    item1 = GoalItem(
        stage=StageId.INTAKE,
        payload="create task",
        intent="create",
        missing_info=["queue", "assignee"],
    )
    item2 = GoalItem(
        stage=StageId.TRANSITION,
        payload="close task",
        intent="close",
        missing_info=["sprint"],
    )
    plan = GoalPlan(items=[item1, item2])
    collected = [info for g in plan.items for info in g.missing_info]
    assert collected == ["queue", "assignee", "sprint"]


def test_no_missing_info_empty():
    item = GoalItem(
        stage=StageId.QUERY,
        payload="find tasks",
        intent="query",
        missing_info=[],
    )
    plan = GoalPlan(items=[item])
    collected = [info for g in plan.items for info in g.missing_info]
    assert collected == []


def test_no_missing_info_empty_lists():
    item1 = GoalItem(
        stage=StageId.QUERY,
        payload="find",
        intent="query",
        missing_info=[],
    )
    item2 = GoalItem(
        stage=StageId.STATUS,
        payload="update",
        intent="status",
        missing_info=[],
    )
    plan = GoalPlan(items=[item1, item2])
    collected = [info for g in plan.items for info in g.missing_info]
    assert collected == []


def test_missing_info_dedup_not_needed():
    item1 = GoalItem(
        stage=StageId.INTAKE,
        payload="create task",
        intent="create",
        missing_info=["assignee"],
    )
    item2 = GoalItem(
        stage=StageId.REORG,
        payload="reassign",
        intent="reorg",
        missing_info=["assignee"],
    )
    plan = GoalPlan(items=[item1, item2])
    collected = [info for g in plan.items for info in g.missing_info]
    assert collected == ["assignee", "assignee"]


def test_missing_info_cleared_after_first_person_resolution():
    item = GoalItem(
        stage=StageId.INTAKE,
        payload="создай мне задачу",
        intent="create",
        entities={"assignee": "мне"},
        missing_info=["исполнитель задачи"],
    )
    plan = GoalPlan(items=[item])
    from core.assignee_resolver import resolve_first_person
    resolved = resolve_first_person("создай мне задачу", tracker_login="kolya")
    assert resolved == "kolya"
    if resolved:
        for g in plan.items:
            if g.entities and "assignee" in g.entities:
                g.entities["assignee"] = resolved
            g.missing_info = [m for m in g.missing_info if "исполнител" not in m.lower() and "assignee" not in m.lower()]
    collected = [info for g in plan.items for info in g.missing_info]
    assert collected == []


def test_missing_info_preserved_when_no_first_person():
    item = GoalItem(
        stage=StageId.INTAKE,
        payload="создай задачу",
        intent="create",
        missing_info=["исполнитель задачи"],
    )
    plan = GoalPlan(items=[item])
    from core.assignee_resolver import resolve_first_person
    resolved = resolve_first_person("создай задачу", tracker_login="kolya")
    assert resolved is None
    collected = [info for g in plan.items for info in g.missing_info]
    assert collected == ["исполнитель задачи"]
