"""Fast path for Telemost links — schedule meeting-capture without the LLM agent."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from core.invocation import InvocationContext
from core.react import AgentResult

# Matches telemost.yandex.ru, telemost.360.yandex.ru, .com, .com.tr — /j/ or /live/ paths.
_TELEMOST_URL_RE = re.compile(
    r"(?:https?://)?telemost(?:\.360)?\.yandex\.(?:ru|com(?:\.tr)?)/(?:j|live)/[A-Za-z0-9_-]+",
    re.IGNORECASE,
)
_TRAILING_PUNCT = ".,);]>'\""


def extract_telemost_url(text: str) -> str | None:
    """Return the first Telemost meeting URL found in *text*, or ``None``."""
    match = _TELEMOST_URL_RE.search(text or "")
    if not match:
        return None
    url = match.group(0)
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url.rstrip(_TRAILING_PUNCT)


def format_meeting_capture_reply(data: dict[str, Any]) -> str:
    meeting_id = data.get("meeting_id")
    return f"🤖 Иду на встречу и включаю запись. Итоги пришлю сюда. (id: {meeting_id})"


async def schedule_meeting_capture(
    telemost_url: str,
    *,
    target_chat_id: str | None = None,
    language: str = "ru-RU",
) -> dict[str, Any]:
    """POST a meeting to meeting-capture and return the JSON body."""
    base = os.getenv("MEETING_CAPTURE_URL", "http://meeting-capture:8003").rstrip("/")
    payload = {
        "telemost_url": telemost_url,
        "consent_ack": True,
        "language": language,
        "target_chat_id": target_chat_id,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{base}/meetings", json=payload)
        response.raise_for_status()
        return response.json()


async def try_meeting_capture_shortcut(
    message: str,
    session_id: str,
    *,
    context: InvocationContext | None = None,
) -> AgentResult | None:
    """If *message* contains a Telemost link, schedule capture and return a result."""
    telemost_url = extract_telemost_url(message)
    if telemost_url is None:
        return None

    target_chat_id = context.chat_id if context is not None else None
    try:
        data = await schedule_meeting_capture(telemost_url, target_chat_id=target_chat_id)
    except Exception as exc:  # noqa: BLE001 — user-facing failure, not a crash
        return AgentResult(
            reply=f"Не удалось отправить бота на встречу: {exc}",
            session_id=session_id,
            steps=[{"kind": "meeting_capture_error", "error": str(exc)}],
        )

    return AgentResult(
        reply=format_meeting_capture_reply(data),
        session_id=session_id,
        steps=[
            {
                "kind": "meeting_capture_scheduled",
                "meeting_id": data.get("meeting_id"),
                "status": data.get("status"),
            }
        ],
    )


__all__ = [
    "extract_telemost_url",
    "format_meeting_capture_reply",
    "schedule_meeting_capture",
    "try_meeting_capture_shortcut",
]
