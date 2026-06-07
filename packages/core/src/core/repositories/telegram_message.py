"""Query repository for TelegramMessage with cursor-based pagination."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from core.models import TelegramMessage

from core.models import TelegramMessage


@dataclass
class MessageCursor:
    """Pagination cursor for message queries.

    Uses sent_at + id for stable pagination across edits.
    """

    sent_at: datetime
    id: uuid.UUID


@dataclass
class MessageQueryOptions:
    """Query options for message listing."""

    team_id: uuid.UUID
    installation_id: uuid.UUID | None = None
    chat_id: uuid.UUID | None = None
    direction: str | None = None  # "inbound" or "outbound"
    access_mode: str | None = None  # "secretary" or "workspace_bot"
    sent_after: datetime | None = None
    sent_before: datetime | None = None
    include_deleted: bool = False
    limit: int = 50


class TelegramMessageRepository:
    """Repository for querying TelegramMessage with cursor-based pagination."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository.

        Args:
            session: Async SQLAlchemy session
        """
        self._session = session

    async def list_messages(
        self, options: MessageQueryOptions, cursor: MessageCursor | None = None
    ) -> tuple[list[TelegramMessage], MessageCursor | None]:
        """Paginated message list with stable cursor-based pagination.

        Cursor uses (sent_at, id) for stability across edits.
        Returns (messages, next_cursor). next_cursor is None when exhausted.

        Args:
            options: Query options for filtering messages
            cursor: Optional cursor for pagination

        Returns:
            Tuple of (list of messages, next cursor or None)
        """
        # Base query
        query = select(TelegramMessage).where(TelegramMessage.team_id == options.team_id)

        # Filters
        if options.installation_id:
            query = query.where(TelegramMessage.installation_id == options.installation_id)
        if options.chat_id:
            query = query.where(TelegramMessage.chat_id == options.chat_id)
        if options.direction:
            query = query.where(TelegramMessage.direction == options.direction)
        if options.access_mode:
            query = query.where(TelegramMessage.access_mode == options.access_mode)
        if options.sent_after:
            query = query.where(TelegramMessage.sent_at >= options.sent_after)
        if options.sent_before:
            query = query.where(TelegramMessage.sent_at <= options.sent_before)
        if not options.include_deleted:
            query = query.where(TelegramMessage.deleted_at.is_(None))

        # Cursor pagination (forward)
        if cursor:
            query = query.where(
                or_(
                    TelegramMessage.sent_at > cursor.sent_at,
                    and_(
                        TelegramMessage.sent_at == cursor.sent_at,
                        TelegramMessage.id > cursor.id,
                    ),
                )
            )

        # Sort + limit (fetch one extra to determine if more results exist)
        query = query.order_by(TelegramMessage.sent_at, TelegramMessage.id).limit(options.limit + 1)

        # Execute query
        result = await self._session.execute(query)
        results = list(result.scalars().all())

        # Determine if there are more results
        if len(results) > options.limit:
            messages = results[: options.limit]
            last = messages[-1]
            next_cursor = MessageCursor(sent_at=last.sent_at, id=last.id)
        else:
            messages = results
            next_cursor = None

        return messages, next_cursor

    async def get_message(self, message_id: uuid.UUID) -> TelegramMessage | None:
        """Get single message by ID.

        Args:
            message_id: UUID of the message

        Returns:
            TelegramMessage or None if not found
        """
        query = select(TelegramMessage).where(TelegramMessage.id == message_id)
        result = await self._session.execute(query)
        return result.scalar_one_or_none()
