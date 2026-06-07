"""Truncation heuristics for backlog_plan / plan_json_looks_invalid.

Regression: a legitimate ellipsis in the MIDDLE of the summary (e.g. a quoted
label «Предположения: …») was wrongly flagged as a truncated message.
"""

from __future__ import annotations

from core.backlog_tools import _text_looks_truncated, plan_json_looks_invalid


def test_mid_text_ellipsis_is_not_truncation():
    text = (
        "Отчёт о создании теперь добавляет строку «Предположения: …». "
        "turn_guards стал тонким shim над графом."
    )
    assert _text_looks_truncated(text) is False


def test_trailing_ellipsis_is_truncation():
    assert _text_looks_truncated("много пунктов …") is True
    assert _text_looks_truncated("много пунктов ...") is True


def test_phrase_markers_are_truncation():
    assert _text_looks_truncated("сделай бота, остальной текст опущен") is True
    assert _text_looks_truncated("план (сокращено)") is True


def test_plain_summary_is_not_truncation():
    assert _text_looks_truncated("обычное длинное саммари без обрезки") is False


def test_plan_json_valid_with_ellipsis_inside_string():
    # An ellipsis inside a string VALUE is content, not truncation.
    assert plan_json_looks_invalid('{"summary": "Предположения: …"}') is False


def test_plan_json_empty_or_unparseable_is_invalid():
    assert plan_json_looks_invalid("") is True
    assert plan_json_looks_invalid("{\"epic\": …}") is True
