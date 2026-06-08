"""Tests for the stage router (rules + LLM-classifier fallback)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from core.stage_graph import StageId
from core.stage_router import classify_stage_llm, detect_stage, detect_stage_rules

# ---------------------------------------------------------------------------
# Rules (R1..R8)
# ---------------------------------------------------------------------------


def test_rule_status_prefix():
    assert detect_stage_rules("Коля: добавил агента, тесты проходят") is StageId.STATUS


def test_rule_board_markers_and_long_text():
    assert detect_stage_rules("Резюме лекции: сделать бота") is StageId.BOARD
    assert detect_stage_rules("оформи доску из этого") is StageId.BOARD
    assert detect_stage_rules("x" * 900) is StageId.BOARD


def test_rule_reorg():
    assert detect_stage_rules("переназначь DARKHORSE-3 на Рому") is StageId.REORG
    assert detect_stage_rules("подними приоритет у задачи") is StageId.REORG


def test_rule_transition():
    assert detect_stage_rules("закрой DARKHORSE-8") is StageId.TRANSITION
    assert detect_stage_rules("переведи в работу DARKHORSE-2") is StageId.TRANSITION


def test_rule_proactive():
    assert detect_stage_rules("проверь просроченные задачи") is StageId.PROACTIVE
    assert detect_stage_rules("найди задачи без исполнителя") is StageId.PROACTIVE
    assert detect_stage_rules("сделай дайджест по доске") is StageId.PROACTIVE


def test_rule_hygiene():
    assert detect_stage_rules("наведи порядок на доске") is StageId.HYGIENE
    assert detect_stage_rules("заполни пропуски в задачах") is StageId.HYGIENE


def test_rule_intake():
    assert detect_stage_rules("создай Коле задачу MCP") is StageId.INTAKE
    assert detect_stage_rules("заведи задачу на нотификации") is StageId.INTAKE


def test_rule_query():
    assert detect_stage_rules("что на доске сейчас") is StageId.QUERY
    assert detect_stage_rules("что у Коли в работе") is StageId.QUERY
    assert detect_stage_rules("покажи статус эпика") is StageId.QUERY


def test_rule_ambiguous_returns_none():
    assert detect_stage_rules("привет, как настроение") is None


def test_status_beats_board_priority():
    # «Имя: …» wins even with long text (R1 before R2)
    msg = "Коля: " + "очень длинный апдейт " * 60
    assert detect_stage_rules(msg) is StageId.STATUS


# ---------------------------------------------------------------------------
# detect_stage (with/without LLM)
# ---------------------------------------------------------------------------


async def test_detect_stage_rules_only_defaults_to_query():
    sid = await detect_stage("нечто непонятное", use_llm=False)
    assert sid is StageId.QUERY


async def test_detect_stage_uses_llm_fallback_when_ambiguous():
    with patch(
        "core.stage_router.classify_stage_llm",
        AsyncMock(return_value=StageId.HYGIENE),
    ) as mock_llm:
        sid = await detect_stage("нечто непонятное", use_llm=True)
    assert sid is StageId.HYGIENE
    mock_llm.assert_awaited_once()


async def test_detect_stage_skips_llm_when_rule_matches():
    with patch("core.stage_router.classify_stage_llm", AsyncMock()) as mock_llm:
        sid = await detect_stage("создай задачу", use_llm=True)
    assert sid is StageId.INTAKE
    mock_llm.assert_not_called()


async def test_classify_stage_llm_parses_enum_token():
    from core.llm import LLMResponse

    fake_resp = LLMResponse(content="BOARD", model="yandexgpt", tool_calls=None)
    with patch("core.llm.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.complete = AsyncMock(return_value=fake_resp)
        instance.close = AsyncMock()
        sid = await classify_stage_llm("какое-то сообщение")
    assert sid is StageId.BOARD


async def test_classify_stage_llm_defaults_query_on_garbage():
    from core.llm import LLMResponse

    fake_resp = LLMResponse(content="мусор", model="yandexgpt", tool_calls=None)
    with patch("core.llm.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.complete = AsyncMock(return_value=fake_resp)
        instance.close = AsyncMock()
        sid = await classify_stage_llm("какое-то сообщение")
    assert sid is StageId.QUERY


async def test_classify_stage_llm_defaults_query_on_error():
    with patch("core.llm.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.complete = AsyncMock(side_effect=RuntimeError("boom"))
        instance.close = AsyncMock()
        sid = await classify_stage_llm("какое-то сообщение")
    assert sid is StageId.QUERY


# ---------------------------------------------------------------------------
# turn plan (plan_turn / decompose_turn_llm)
# ---------------------------------------------------------------------------


async def test_plan_turn_rules_only_single_item():
    from core.turn_plan import plan_turn

    plan = await plan_turn("создай задачу MCP", use_llm=False)
    assert not plan.is_dialog
    assert len(plan.items) == 1
    assert plan.items[0].stage is StageId.INTAKE


async def test_decompose_turn_llm_multi_ordered():
    from core.llm import LLMResponse
    from core.turn_plan import decompose_turn_llm

    payload = (
        '{"scenarios": ['
        '{"stage": "INTAKE", "payload": "создай задачу A", "rationale": "create"},'
        '{"stage": "TRANSITION", "payload": "закрой B", "rationale": "close"}'
        "]}"
    )
    fake_resp = LLMResponse(content=payload, model="yandexgpt", tool_calls=None)
    with patch("core.llm.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.complete = AsyncMock(return_value=fake_resp)
        instance.close = AsyncMock()
        plan = await decompose_turn_llm("создай A и закрой B")
    assert len(plan.items) == 2
    assert plan.items[0].stage is StageId.INTAKE
    assert plan.items[1].stage is StageId.TRANSITION


async def test_decompose_turn_llm_dialog_only():
    from core.llm import LLMResponse
    from core.turn_plan import decompose_turn_llm

    payload = '{"scenarios": [{"stage": "DIALOG", "payload": "привет"}]}'
    fake_resp = LLMResponse(content=payload, model="yandexgpt", tool_calls=None)
    with patch("core.llm.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.complete = AsyncMock(return_value=fake_resp)
        instance.close = AsyncMock()
        plan = await decompose_turn_llm("привет")
    assert plan.is_dialog


async def test_decompose_turn_llm_drops_dialog_when_actionable():
    from core.llm import LLMResponse
    from core.turn_plan import decompose_turn_llm

    payload = (
        '{"scenarios": ['
        '{"stage": "DIALOG", "payload": "привет"},'
        '{"stage": "INTAKE", "payload": "создай задачу"}'
        "]}"
    )
    fake_resp = LLMResponse(content=payload, model="yandexgpt", tool_calls=None)
    with patch("core.llm.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.complete = AsyncMock(return_value=fake_resp)
        instance.close = AsyncMock()
        plan = await decompose_turn_llm("привет и создай задачу")
    assert not plan.is_dialog
    assert len(plan.items) == 1
    assert plan.items[0].stage is StageId.INTAKE


async def test_decompose_turn_llm_bad_json_falls_back_to_rules():
    from core.llm import LLMResponse
    from core.turn_plan import decompose_turn_llm

    fake_resp = LLMResponse(content="not json", model="yandexgpt", tool_calls=None)
    with patch("core.llm.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.complete = AsyncMock(return_value=fake_resp)
        instance.close = AsyncMock()
        plan = await decompose_turn_llm("создай задачу MCP")
    assert plan.items[0].stage is StageId.INTAKE
