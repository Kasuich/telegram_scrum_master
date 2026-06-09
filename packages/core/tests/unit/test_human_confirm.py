from __future__ import annotations

from core.react import _TOOL_LABELS


def test_tool_labels_contains_tracker_create_issue():
    assert _TOOL_LABELS["tracker_create_issue"] == "Создание задачи в Трекере"


def test_tool_labels_contains_tracker_close_issue():
    assert _TOOL_LABELS["tracker_close_issue"] == "Закрытие задачи"


def test_tool_labels_contains_tracker_transition_issue():
    assert _TOOL_LABELS["tracker_transition_issue"] == "Смена статуса задачи"


def test_tool_labels_contains_tracker_comment_issue():
    assert _TOOL_LABELS["tracker_comment_issue"] == "Комментарий к задаче"


def test_tool_labels_contains_tracker_create_sprint():
    assert _TOOL_LABELS["tracker_create_sprint"] == "Создание спринта"


def test_tool_labels_falls_back_to_tool_name():
    assert _TOOL_LABELS.get("unknown_tool", "unknown_tool") == "unknown_tool"


def test_confirm_prompt_russian_format():
    label = _TOOL_LABELS.get("tracker_create_issue", "tracker_create_issue")
    prompt = (
        f"Запрос на действие: {label}\n"
        f"Риск: medium\n"
        f"Параметры: queue=TEST, summary=bug\n"
        f"Разрешить?"
    )
    assert "Создание задачи в Трекере" in prompt
    assert "Риск:" in prompt
    assert "Разрешить?" in prompt


def test_confirm_prompt_unknown_tool_uses_name():
    assert _TOOL_LABELS.get("custom_tool", "custom_tool") == "custom_tool"
