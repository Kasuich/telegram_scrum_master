"""Tests for Tracker comment formatting helpers."""

from __future__ import annotations

from core.comment_format import (
    TRACKER_COMMENT_PREFIX,
    build_tracker_comment_summarize_message,
    extract_status_author,
)


def test_extract_status_author():
    assert extract_status_author("Коля: сделал фичу") == "Коля"
    assert extract_status_author("без автора") is None


def test_build_tracker_comment_message():
    msg = build_tracker_comment_summarize_message("Коля: добавил агент")
    assert msg.startswith(TRACKER_COMMENT_PREFIX)
    assert "Автор статуса: Коля" in msg
    assert "Коля: добавил агент" in msg
