"""Helpers for formatting Tracker comments via meeting_summarizer."""

from __future__ import annotations

import re

TRACKER_COMMENT_PREFIX = "ОФОРМИ КОММЕНТАРИЙ К ЗАДАЧЕ В ТРЕКЕРЕ"

_STATUS_AUTHOR_RE = re.compile(
    r"^([А-Яа-яA-Za-z][А-Яа-яA-Za-z.\-]*):\s*",
    re.UNICODE,
)


def extract_status_author(message: str) -> str | None:
    m = _STATUS_AUTHOR_RE.match(message.strip())
    return m.group(1) if m else None


def build_tracker_comment_summarize_message(user_message: str) -> str:
    """Wrap chat status text for meeting_summarizer ticket-comment mode."""
    author = extract_status_author(user_message)
    author_line = f"Автор статуса: {author}\n" if author else ""
    return (
        f"{TRACKER_COMMENT_PREFIX}\n"
        f"{author_line}"
        "Исходный текст статуса (оформи для комментария в карточке задачи):\n\n"
        f"{user_message.strip()}"
    )
