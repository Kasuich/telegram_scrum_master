"""Tools for scheduling and reading Telemost meeting captures."""

from __future__ import annotations

import os
from typing import Any

import httpx
from core.exceptions import ToolExecutionError
from core.invocation import get_current_invocation_context
from core.tools import platform_tool


def _capture_url() -> str:
    return os.getenv("MEETING_CAPTURE_URL", "http://meeting-capture:8003").rstrip("/")


def register_meeting_capture_tools(svc: Any) -> None:
    """Register meeting-capture tools.

    ``svc`` is accepted for the same startup factory pattern as other tools.
    It is not used directly because capture is an external deterministic service.
    """

    del svc

    @platform_tool(name="schedule_meeting_bot", risk="medium", scopes=["meeting:capture"])
    async def schedule_meeting_bot(
        url: str,
        starts_at: str = "",
        title: str = "",
        consent_ack: bool = True,
        language: str = "ru-RU",
    ) -> dict[str, Any]:
        """Schedule a Telemost recording bot for a meeting link.

        Args:
            url: Telemost meeting URL.
            starts_at: Optional ISO datetime. Empty means join immediately.
            title: Optional meeting title.
            consent_ack: Must be true; confirms participants consent to a visible recorder.
            language: STT language, default ru-RU.
        """
        if not consent_ack:
            raise ToolExecutionError("schedule_meeting_bot: consent_ack must be true")

        # Capture the chat the request came from so the meeting summary can be
        # delivered back there. The pm_agent runs inside an InvocationContext
        # whose chat_id is the external Telegram chat id (see telegram_bridge).
        ctx = get_current_invocation_context()
        target_chat_id = ctx.chat_id if ctx is not None else None

        payload: dict[str, Any] = {
            "telemost_url": url,
            "starts_at": starts_at or None,
            "title": title or None,
            "consent_ack": consent_ack,
            "language": language,
            "target_chat_id": target_chat_id,
        }
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(f"{_capture_url()}/meetings", json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise ToolExecutionError(
                f"schedule_meeting_bot: capture service returned "
                f"{exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ToolExecutionError(f"schedule_meeting_bot: capture service error: {exc}") from exc

    @platform_tool(name="get_meeting_transcript", risk="low", scopes=["meeting:capture"])
    async def get_meeting_transcript(meeting_id: str) -> dict[str, Any]:
        """Fetch a ready transcript by meeting_id."""
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(f"{_capture_url()}/meetings/{meeting_id}/transcript")
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise ToolExecutionError(
                f"get_meeting_transcript: capture service returned "
                f"{exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ToolExecutionError(
                f"get_meeting_transcript: capture service error: {exc}"
            ) from exc


__all__ = ["register_meeting_capture_tools"]
