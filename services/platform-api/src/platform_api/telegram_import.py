"""
Telegram Desktop JSON import pipeline.

Parses Telegram Desktop export format and normalizes messages into
the telegram_messages table with dedupe by (installation_id, chat_id, external_message_id).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from core.models import (
    TelegramChat,
    TelegramImportJob,
    TelegramMessage,
)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_CHUNK_SIZE = 500
_MAX_TEXT_LENGTH = 4096
_MAX_CAPTION_LENGTH = 1024
_SUPPORTED_TYPES = frozenset(
    {
        "message",
        "service",
        "photo",
        "video",
        "document",
        "audio",
        "voice",
        "sticker",
        "video_note",
        "contact",
        "location",
        "poll",
        "game",
        "migration",
    }
)


@dataclass
class ImportStats:
    created: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class ParsedMessage:
    external_message_id: str
    external_chat_id: str
    message_kind: str
    text: str | None
    caption: str | None
    sent_at: datetime | None
    edited_at: datetime | None
    actor_external_id: str | None
    actor_name: str | None
    reply_to_message_id: str | None
    media_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None


def _parse_timestamp(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromtimestamp(int(date_str), tz=timezone.utc)
        except (ValueError, TypeError):
            return None


def _extract_text(text: str | list[dict[str, Any]] | None) -> str | None:
    if text is None:
        return None
    if isinstance(text, str):
        return text[:_MAX_TEXT_LENGTH]
    if isinstance(text, list):
        parts = []
        for entity in text:
            if isinstance(entity, dict) and entity.get("type") == "plain":
                parts.append(entity.get("text", ""))
        result = "".join(parts)
        return result[:_MAX_TEXT_LENGTH] if result else None
    return None


def _message_kind_from_type(msg_type: str, media_type: str | None) -> str:
    if media_type:
        return media_type
    kind_map = {
        "message": "text",
        "service": "service",
        "photo": "photo",
        "video": "video",
        "document": "document",
        "audio": "audio",
        "voice": "voice",
        "sticker": "sticker",
        "video_note": "video_note",
        "contact": "contact",
        "location": "location",
        "poll": "poll",
        "game": "game",
        "migration": "service",
    }
    return kind_map.get(msg_type, "text")


def _build_media_json(msg: dict[str, Any]) -> dict[str, Any] | None:
    media_fields = ["photo", "file", "sticker", "thumbnail", "location", "poll", "contact"]
    for field_name in media_fields:
        value = msg.get(field_name)
        if value and isinstance(value, dict):
            return {
                "media_type": field_name,
                "file_id": value.get("id") or value.get("file_reference"),
                "file_unique_id": value.get("access_hash"),
                "mime_type": msg.get("mime_type"),
                "width": msg.get("width"),
                "height": msg.get("height"),
                "duration": msg.get("duration_seconds"),
                "title": value.get("title"),
                "performer": value.get("performer"),
                "size": value.get("size"),
                "local_id": value.get("local_id"),
            }
    return None


def parse_export_messages(chat_data: dict[str, Any]) -> AsyncIterator[ParsedMessage]:
    """
    Parse Telegram Desktop export JSON and yield normalized messages.

    Args:
        chat_data: Dictionary with chat data including 'id' and 'messages' list

    Yields:
        ParsedMessage objects with normalized message data
    """
    external_chat_id = str(chat_data.get("id", ""))
    messages = chat_data.get("messages", [])

    for raw_msg in messages:
        msg_type = raw_msg.get("type", "message")
        if msg_type not in _SUPPORTED_TYPES:
            continue

        msg_id = str(raw_msg.get("id", ""))
        text = _extract_text(raw_msg.get("text"))
        caption = None
        message_kind = _message_kind_from_type(msg_type, raw_msg.get("media_type"))

        actor_external_id = None
        actor_name = None
        reply_to = (
            str(raw_msg["reply_to_message_id"]) if raw_msg.get("reply_to_message_id") else None
        )

        if msg_type not in ("service", "migration"):
            actor_external_id = str(raw_msg.get("from_id", ""))
            actor_name = raw_msg.get("from")
            media_kinds = ("photo", "video", "document", "audio", "voice")
            caption = text if message_kind in media_kinds else None

        media_json = _build_media_json(raw_msg)

        if caption:
            caption = caption[:_MAX_CAPTION_LENGTH]
            if message_kind == "text":
                text = text[:_MAX_TEXT_LENGTH] if text else None

        metadata_json = {
            "actor_external_id": actor_external_id,
            "actor_name": actor_name,
            "import_source_type": msg_type,
        }

        yield ParsedMessage(
            external_message_id=msg_id,
            external_chat_id=external_chat_id,
            message_kind=message_kind,
            text=text if message_kind == "text" else None,
            caption=caption,
            sent_at=_parse_timestamp(raw_msg.get("date")),
            edited_at=None,
            actor_external_id=actor_external_id,
            actor_name=actor_name,
            reply_to_message_id=reply_to,
            media_json=media_json,
            metadata_json=metadata_json,
        )


async def _dedupe_keys_exist(
    session: AsyncSession,
    installation_id: uuid.UUID,
    external_chat_id: str,
    message_ids: set[str],
) -> set[str]:
    """
    Return subset of message_ids that already exist in DB.

    Args:
        session: Database session
        installation_id: Installation UUID
        external_chat_id: External chat ID string
        message_ids: Set of message IDs to check

    Returns:
        Set of message IDs that already exist
    """
    stmt = select(TelegramMessage.external_message_id).where(
        TelegramMessage.installation_id == installation_id,
        TelegramMessage.external_chat_id == external_chat_id,
        TelegramMessage.external_message_id.in_(message_ids),
    )
    result = await session.execute(stmt)
    return set(result.scalars().all())


async def run_import(
    session: AsyncSession,
    job: TelegramImportJob,
    export_data: dict[str, Any],
    installation_id: uuid.UUID,
    chat: TelegramChat,
) -> ImportStats:
    """
    Run full import pipeline: parse, dedupe, insert chunks.

    Args:
        session: Database session
        job: Import job to update
        export_data: Full export JSON data
        installation_id: Installation UUID
        chat: TelegramChat object

    Returns:
        ImportStats with counts of created/skipped/failed messages
    """
    stats = ImportStats()
    job.status = "processing"
    job.started_at = datetime.now(tz=timezone.utc)
    session.add(job)
    await session.flush()

    chats = export_data.get("chats", [])
    if not chats:
        job.status = "failed"
        job.error_message = "Export JSON has no 'chats' field"
        await session.commit()
        return stats

    target_chat = chats[0]
    job.total_messages = len(target_chat.get("messages", []))
    await session.flush()

    msg_ids: set[str] = set()
    parsed_messages: list[ParsedMessage] = []

    async for parsed in parse_export_messages(target_chat):
        parsed_messages.append(parsed)
        msg_ids.add(parsed.external_message_id)

    existing = await _dedupe_keys_exist(session, installation_id, chat.external_chat_id, msg_ids)

    for parsed in parsed_messages:
        if parsed.external_message_id in existing:
            stats.skipped += 1
            continue

        msg = TelegramMessage(
            team_id=job.team_id,
            installation_id=installation_id,
            chat_id=chat.id,
            telegram_user_id=None,
            business_connection_ref_id=None,
            raw_update_id=None,
            direction="inbound",
            access_mode="import",
            external_chat_id=parsed.external_chat_id,
            external_message_id=parsed.external_message_id,
            external_thread_id=None,
            reply_to_external_message_id=parsed.reply_to_message_id,
            message_kind=parsed.message_kind,
            import_source="telegram_desktop",
            text=parsed.text,
            caption=parsed.caption,
            sent_at=parsed.sent_at,
            edited_at=parsed.edited_at,
            deleted_at=None,
            media_json=parsed.media_json,
            metadata_json=parsed.metadata_json,
        )
        session.add(msg)
        stats.created += 1
        job.processed_messages += 1

        if stats.created % _CHUNK_SIZE == 0:
            await session.flush()

    try:
        await session.flush()
        job.status = "completed"
        job.completed_at = datetime.now(tz=timezone.utc)
    except Exception as exc:
        job.status = "failed"
        job.error_message = str(exc)[:500]
        stats.errors.append(str(exc))

    job.created_messages = stats.created
    job.skipped_messages = stats.skipped
    job.failed_messages = stats.failed
    await session.commit()
    return stats


class ImportRequest(BaseModel):
    """Request model for import operation."""

    team_id: str
    installation_id: str
    chat_id: str


class ImportReport(BaseModel):
    """Report model for import operation results."""

    job_id: str
    status: str
    total_messages: int
    created_messages: int
    skipped_messages: int
    failed_messages: int
    error_message: str | None
