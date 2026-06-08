"""Enqueue a Telegram delivery directly into the shared outbox table.

meeting-capture has no direct line to the Telegram gateway, but both share the
database. We insert a ``telegram_outbox`` row (status=pending); the gateway
leases pending rows by ``target_chat_id`` and delivers them — ``installation_id``
and the chat FK are optional (see telegram_bridge.lease_outbox / _deliver_item).
"""

from __future__ import annotations

import logging
import uuid

from core.db import get_session
from core.models import TelegramOutbox

logger = logging.getLogger(__name__)


async def enqueue_telegram_message(
    *,
    team_id: uuid.UUID,
    target_chat_id: str,
    text: str,
    category: str = "meeting_summary",
    dedupe_key: str | None = None,
    priority: int = 100,
) -> uuid.UUID | None:
    """Insert a pending sendMessage outbox row. Returns the row id, or None."""
    if not target_chat_id or not text.strip():
        return None
    async with get_session() as session:
        outbox = TelegramOutbox(
            id=uuid.uuid4(),
            team_id=team_id,
            category=category,
            target_chat_id=str(target_chat_id),
            dedupe_key=dedupe_key,
            priority=priority,
            status="pending",
            attempts=0,
            payload={"method": "sendMessage", "text": text},
        )
        session.add(outbox)
        await session.flush()
        return outbox.id


__all__ = ["enqueue_telegram_message"]
