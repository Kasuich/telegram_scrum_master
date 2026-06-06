"""Internal Telegram bridge endpoints for gateway -> main communication."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from core.db import get_session
from core.invocation import InvocationContext
from core.models import (
    TelegramBusinessConnection,
    TelegramChat,
    TelegramInstallation,
    TelegramMessage,
    TelegramOutbox,
    TelegramUpdate,
    TelegramUser,
)
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_api import rpc_client

router = APIRouter(prefix="/internal/telegram/v1", tags=["telegram-bridge"])

_SEEN_NONCES: dict[str, float] = {}
_DEFAULT_NONCE_TTL = 300
_DEFAULT_LEASE_LIMIT = 20
_DEFAULT_LEASE_SECONDS = 60


class HeartbeatRequest(BaseModel):
    gateway_id: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=64)
    queue_depth: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestEventRequest(BaseModel):
    team_id: str
    installation_id: str
    update_id: int
    payload: dict[str, Any]
    received_at: datetime | None = None


class LeaseRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=255)
    limit: int = Field(default=_DEFAULT_LEASE_LIMIT, ge=1, le=100)
    lease_seconds: int = Field(default=_DEFAULT_LEASE_SECONDS, ge=5, le=3600)


class LeaseItem(BaseModel):
    delivery_id: str
    team_id: str
    installation_id: str | None = None
    category: str
    target_chat_id: str | None = None
    target_user_id: str | None = None
    payload: dict[str, Any]
    business_connection_id: str | None = None
    lease_expires_at: datetime | None = None


class LeaseResponse(BaseModel):
    items: list[LeaseItem] = Field(default_factory=list)


class AckRequest(BaseModel):
    status: str = Field(pattern="^(sent|retry|dead_letter|ambiguous|cancelled)$")
    provider_message_id: str | None = None
    last_error: str | None = None
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86400)


def _message_text(message_payload: dict[str, Any]) -> str:
    value = message_payload.get("text") or message_payload.get("caption") or ""
    return str(value).strip()


def _actor_display_name(
    telegram_user: TelegramUser | None,
    message_payload: dict[str, Any],
) -> str | None:
    if telegram_user is not None:
        parts = [telegram_user.first_name, telegram_user.last_name]
        full_name = " ".join(part for part in parts if part).strip()
        return full_name or telegram_user.username or telegram_user.external_user_id

    from_payload = message_payload.get("from") or {}
    parts = [from_payload.get("first_name"), from_payload.get("last_name")]
    full_name = " ".join(str(part) for part in parts if part).strip()
    return full_name or from_payload.get("username")


def _bot_username(installation: TelegramInstallation) -> str | None:
    raw = installation.settings.get("bot_username") if installation.settings else None
    if raw:
        return str(raw).lstrip("@")
    if installation.alias:
        return str(installation.alias).lstrip("@")
    return None


def _message_mentions_bot(
    installation: TelegramInstallation,
    message_payload: dict[str, Any],
) -> bool:
    text = _message_text(message_payload)
    if not text:
        return False
    if text.startswith("/"):
        return True

    bot_username = _bot_username(installation)
    entities: list[dict[str, Any]] = []
    for key in ("entities", "caption_entities"):
        raw = message_payload.get(key) or []
        entities.extend(item for item in raw if isinstance(item, dict))

    for entity in entities:
        entity_type = entity.get("type")
        if entity_type == "bot_command":
            return True
        if entity_type != "mention" or not bot_username:
            continue
        try:
            offset = int(entity.get("offset", 0))
            length = int(entity.get("length", 0))
        except (TypeError, ValueError):
            continue
        fragment = text[offset : offset + length].lstrip("@").lower()
        if fragment == bot_username.lower():
            return True

    if bot_username and f"@{bot_username.lower()}" in text.lower():
        return True
    return False


def _message_replies_to_bot(
    installation: TelegramInstallation,
    message_payload: dict[str, Any],
) -> bool:
    reply_to = message_payload.get("reply_to_message") or {}
    from_payload = reply_to.get("from") or {}
    if not from_payload:
        return False

    if installation.external_bot_id and (
        str(from_payload.get("id")) == str(installation.external_bot_id)
    ):
        return True
    return bool(from_payload.get("is_bot"))


def _should_route_message(
    installation: TelegramInstallation,
    chat: TelegramChat,
    message_payload: dict[str, Any],
) -> bool:
    if not chat.active:
        return False
    if not _message_text(message_payload):
        return False

    mode = (chat.ingest_mode or "disabled").strip().lower()
    if mode in {"disabled", "archive_only", "correspondence"}:
        return False
    if mode == "direct":
        return chat.type == "private"
    if mode == "mentions":
        return _message_mentions_bot(installation, message_payload) or _message_replies_to_bot(
            installation,
            message_payload,
        )
    return False


def _telegram_session_id(
    installation: TelegramInstallation,
    message: TelegramMessage,
) -> str:
    return (
        f"telegram:{installation.id}:{message.external_chat_id}:{message.external_thread_id or '0'}"
    )


def _build_invocation_context(
    installation: TelegramInstallation,
    chat: TelegramChat,
    message: TelegramMessage,
    telegram_user: TelegramUser | None,
    message_payload: dict[str, Any],
) -> InvocationContext:
    reply_to_message = message_payload.get("reply_to_message") or {}
    return InvocationContext(
        channel="telegram",
        team_id=str(installation.team_id),
        session_id=_telegram_session_id(installation, message),
        installation_id=str(installation.id),
        chat_id=message.external_chat_id,
        message_id=message.external_message_id,
        thread_id=message.external_thread_id,
        actor_external_id=telegram_user.external_user_id if telegram_user is not None else None,
        actor_display_name=_actor_display_name(telegram_user, message_payload),
        reply_to_message_id=(
            str(reply_to_message.get("message_id")) if reply_to_message.get("message_id") else None
        ),
        metadata={
            "chat_type": chat.type,
            "ingest_mode": chat.ingest_mode,
            "access_mode": chat.access_mode,
        },
    )


async def _enqueue_agent_result(
    session: AsyncSession,
    *,
    installation: TelegramInstallation,
    chat: TelegramChat,
    message: TelegramMessage,
    telegram_user: TelegramUser | None,
    result: Any,
) -> TelegramOutbox | None:
    if result.pending_confirm is not None:
        text = result.pending_confirm.prompt
        category = "confirmation"
        dedupe_key = f"telegram:confirm:{message.id}:{result.pending_confirm.confirm_id}"
        metadata = {"confirm_id": result.pending_confirm.confirm_id}
    elif result.reply:
        text = result.reply
        category = "agent_reply"
        dedupe_key = f"telegram:reply:{message.id}"
        metadata = {}
    else:
        return None

    outbox = TelegramOutbox(
        team_id=installation.team_id,
        installation_id=installation.id,
        chat_id=chat.id,
        category=category,
        target_chat_id=chat.external_chat_id,
        target_user_id=telegram_user.external_user_id if telegram_user is not None else None,
        dedupe_key=dedupe_key,
        priority=100,
        status="pending",
        attempts=0,
        payload={
            "method": "sendMessage",
            "text": text,
            "message_thread_id": message.external_thread_id,
            "reply_to_message_id": message.external_message_id,
            "metadata": metadata,
        },
    )
    session.add(outbox)
    await session.flush()
    return outbox


async def _route_inbound_message(
    session: AsyncSession,
    *,
    installation: TelegramInstallation,
    chat: TelegramChat,
    message: TelegramMessage,
    telegram_user: TelegramUser | None,
    message_payload: dict[str, Any],
) -> dict[str, Any] | None:
    if not _should_route_message(installation, chat, message_payload):
        return None

    context = _build_invocation_context(
        installation,
        chat,
        message,
        telegram_user,
        message_payload,
    )
    result = await rpc_client.invoke(
        "pm_agent",
        _message_text(message_payload),
        _telegram_session_id(installation, message),
        context=context,
    )
    outbox = await _enqueue_agent_result(
        session,
        installation=installation,
        chat=chat,
        message=message,
        telegram_user=telegram_user,
        result=result,
    )
    return {
        "session_id": context.session_id,
        "outbox_id": str(outbox.id) if outbox is not None else None,
        "reply": result.reply,
        "pending_confirm_id": (
            result.pending_confirm.confirm_id if result.pending_confirm is not None else None
        ),
    }


def _bridge_keys() -> dict[str, str]:
    raw = os.getenv("TELEGRAM_BRIDGE_HMAC_KEYS", "").strip()
    keys: dict[str, str] = {}
    if not raw:
        return keys
    for chunk in raw.split(","):
        part = chunk.strip()
        if not part or ":" not in part:
            continue
        key_id, secret = part.split(":", 1)
        if key_id and secret:
            keys[key_id.strip()] = secret.strip()
    return keys


def _nonce_ttl() -> int:
    raw = os.getenv("TELEGRAM_BRIDGE_NONCE_TTL", str(_DEFAULT_NONCE_TTL)).strip()
    try:
        return max(30, int(raw))
    except ValueError:
        return _DEFAULT_NONCE_TTL


def _prune_nonces(now_ts: float) -> None:
    ttl = _nonce_ttl()
    expired = [nonce for nonce, seen_at in _SEEN_NONCES.items() if now_ts - seen_at > ttl]
    for nonce in expired:
        _SEEN_NONCES.pop(nonce, None)


def _body_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _signature_payload(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    parts = [method.upper(), path, timestamp, nonce, _body_sha256(body)]
    return "\n".join(parts).encode("utf-8")


def _sign(secret: str, method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    payload = _signature_payload(method, path, timestamp, nonce, body)
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


async def verify_bridge_request(request: Request) -> None:
    keys = _bridge_keys()
    if not keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram bridge keys are not configured",
        )

    key_id = request.headers.get("X-Telegram-Bridge-Key-Id", "")
    timestamp = request.headers.get("X-Telegram-Bridge-Timestamp", "")
    nonce = request.headers.get("X-Telegram-Bridge-Nonce", "")
    signature = request.headers.get("X-Telegram-Bridge-Signature", "")
    secret = keys.get(key_id)
    if not key_id or not timestamp or not nonce or not signature or not secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bridge auth")

    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid timestamp",
        ) from exc

    now_ts = time.time()
    ttl = _nonce_ttl()
    if abs(now_ts - ts) > ttl:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Expired timestamp")

    _prune_nonces(now_ts)
    if nonce in _SEEN_NONCES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Replay detected")

    body = await request.body()
    expected = _sign(secret, request.method, request.url.path, timestamp, nonce, body)
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    _SEEN_NONCES[nonce] = now_ts


def _epoch_to_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _message_payload_kind(payload: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    for key in (
        "message",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "business_message",
        "edited_business_message",
    ):
        value = payload.get(key)
        if isinstance(value, dict):
            return key, value
    return "", None


async def _get_installation(
    session: AsyncSession,
    installation_id: str,
) -> TelegramInstallation | None:
    return await session.get(TelegramInstallation, uuid.UUID(installation_id))


async def _upsert_chat(
    session: AsyncSession,
    installation_id: uuid.UUID,
    chat_payload: dict[str, Any],
) -> TelegramChat:
    external_chat_id = str(chat_payload.get("id"))
    stmt = select(TelegramChat).where(
        TelegramChat.installation_id == installation_id,
        TelegramChat.external_chat_id == external_chat_id,
    )
    chat = (await session.execute(stmt)).scalar_one_or_none()
    if chat is None:
        chat = TelegramChat(
            installation_id=installation_id,
            external_chat_id=external_chat_id,
            type=str(chat_payload.get("type") or "unknown"),
            title=chat_payload.get("title"),
            username=chat_payload.get("username"),
            ingest_mode="disabled",
            access_mode="workspace_bot",
            send_policy={},
            active=True,
            metadata_json={},
        )
        session.add(chat)
    else:
        chat.type = str(chat_payload.get("type") or chat.type)
        chat.title = chat_payload.get("title") or chat.title
        chat.username = chat_payload.get("username") or chat.username
    await session.flush()
    return chat


async def _upsert_user(
    session: AsyncSession,
    user_payload: dict[str, Any] | None,
) -> TelegramUser | None:
    if not isinstance(user_payload, dict) or user_payload.get("id") is None:
        return None
    external_user_id = str(user_payload["id"])
    stmt = select(TelegramUser).where(TelegramUser.external_user_id == external_user_id)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None:
        user = TelegramUser(
            external_user_id=external_user_id,
            username=user_payload.get("username"),
            first_name=user_payload.get("first_name"),
            last_name=user_payload.get("last_name"),
            language_code=user_payload.get("language_code"),
            is_bot=bool(user_payload.get("is_bot", False)),
            is_blocked=False,
            metadata_json={},
        )
        session.add(user)
    else:
        user.username = user_payload.get("username") or user.username
        user.first_name = user_payload.get("first_name") or user.first_name
        user.last_name = user_payload.get("last_name") or user.last_name
        user.language_code = user_payload.get("language_code") or user.language_code
        user.is_bot = bool(user_payload.get("is_bot", user.is_bot))
    await session.flush()
    return user


async def _find_business_connection(
    session: AsyncSession,
    installation_id: uuid.UUID,
    payload: dict[str, Any],
) -> TelegramBusinessConnection | None:
    external_id = payload.get("business_connection_id")
    if not external_id:
        return None
    stmt = select(TelegramBusinessConnection).where(
        TelegramBusinessConnection.installation_id == installation_id,
        TelegramBusinessConnection.business_connection_id == str(external_id),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def ingest_event(session: AsyncSession, data: IngestEventRequest) -> dict[str, Any]:
    installation = await _get_installation(session, data.installation_id)
    if installation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Installation not found")
    if str(installation.team_id) != data.team_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Installation/team mismatch",
        )

    stmt = select(TelegramUpdate).where(
        TelegramUpdate.installation_id == installation.id,
        TelegramUpdate.update_id == data.update_id,
    )
    update = (await session.execute(stmt)).scalar_one_or_none()
    duplicate = update is not None
    if update is None:
        update = TelegramUpdate(
            installation_id=installation.id,
            update_id=data.update_id,
            payload=data.payload,
            payload_hash=_body_sha256(str(data.payload).encode("utf-8")),
            status="pending",
            received_at=data.received_at or datetime.now(timezone.utc),
            processed_at=None,
        )
        session.add(update)
        await session.flush()

    normalized_message_id: str | None = None
    routing_result: dict[str, Any] | None = None
    kind, message_payload = _message_payload_kind(data.payload)
    if message_payload is not None:
        chat = await _upsert_chat(session, installation.id, message_payload.get("chat") or {})
        telegram_user = await _upsert_user(session, message_payload.get("from"))
        business_connection = await _find_business_connection(
            session,
            installation.id,
            data.payload,
        )
        external_message_id = str(message_payload.get("message_id"))

        msg_stmt = select(TelegramMessage).where(
            TelegramMessage.installation_id == installation.id,
            TelegramMessage.external_chat_id == chat.external_chat_id,
            TelegramMessage.external_message_id == external_message_id,
        )
        msg = (await session.execute(msg_stmt)).scalar_one_or_none()
        message_created = msg is None
        if msg is None:
            msg = TelegramMessage(
                team_id=installation.team_id,
                installation_id=installation.id,
                chat_id=chat.id,
                telegram_user_id=telegram_user.id if telegram_user is not None else None,
                business_connection_ref_id=(
                    business_connection.id if business_connection is not None else None
                ),
                raw_update_id=update.id,
                direction="inbound",
                access_mode="secretary" if business_connection is not None else "workspace_bot",
                external_chat_id=chat.external_chat_id,
                external_message_id=external_message_id,
                external_thread_id=(
                    str(message_payload.get("message_thread_id"))
                    if message_payload.get("message_thread_id") is not None
                    else None
                ),
                reply_to_external_message_id=(
                    str((message_payload.get("reply_to_message") or {}).get("message_id"))
                    if message_payload.get("reply_to_message")
                    else None
                ),
                message_kind=kind,
                import_source=None,
                text=message_payload.get("text"),
                caption=message_payload.get("caption"),
                sent_at=_epoch_to_dt(message_payload.get("date")),
                edited_at=_epoch_to_dt(message_payload.get("edit_date")),
                deleted_at=None,
                media_json={},
                metadata_json={"raw_kind": kind},
            )
            session.add(msg)
            await session.flush()
        normalized_message_id = str(msg.id)
        if not duplicate and message_created:
            routing_result = await _route_inbound_message(
                session,
                installation=installation,
                chat=chat,
                message=msg,
                telegram_user=telegram_user,
                message_payload=message_payload,
            )

    update.status = "processed"
    update.processed_at = datetime.now(timezone.utc)
    await session.flush()
    return {
        "update_id": str(update.id),
        "duplicate": duplicate,
        "normalized_message_id": normalized_message_id,
        "routing": routing_result,
    }


async def lease_outbox(session: AsyncSession, data: LeaseRequest) -> list[LeaseItem]:
    now = datetime.now(timezone.utc)
    stmt: Select[tuple[TelegramOutbox]] = (
        select(TelegramOutbox)
        .where(
            TelegramOutbox.status.in_(("pending", "retry")),
            or_(TelegramOutbox.next_attempt_at.is_(None), TelegramOutbox.next_attempt_at <= now),
            or_(
                TelegramOutbox.lease_expires_at.is_(None),
                TelegramOutbox.lease_expires_at <= now,
            ),
        )
        .order_by(TelegramOutbox.priority.asc(), TelegramOutbox.created_at.asc())
        .limit(data.limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    leased: list[LeaseItem] = []
    for row in rows:
        row.status = "leased"
        row.lease_owner = data.worker_id
        row.lease_expires_at = now + timedelta(seconds=data.lease_seconds)
        leased.append(
            LeaseItem(
                delivery_id=str(row.id),
                team_id=str(row.team_id),
                installation_id=str(row.installation_id) if row.installation_id else None,
                category=row.category,
                target_chat_id=row.target_chat_id,
                target_user_id=row.target_user_id,
                payload=dict(row.payload or {}),
                business_connection_id=(
                    str(row.business_connection_ref_id) if row.business_connection_ref_id else None
                ),
                lease_expires_at=row.lease_expires_at,
            )
        )
    await session.flush()
    return leased


async def ack_outbox(
    session: AsyncSession,
    delivery_id: str,
    data: AckRequest,
) -> dict[str, Any]:
    row = await session.get(TelegramOutbox, uuid.UUID(delivery_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found")

    row.status = data.status
    row.provider_message_id = data.provider_message_id
    row.last_error = data.last_error
    row.lease_owner = None
    row.lease_expires_at = None
    if data.status == "retry":
        row.attempts += 1
        delay = data.retry_after_seconds or 60
        row.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
    else:
        row.next_attempt_at = None
    await session.flush()
    return {"delivery_id": str(row.id), "status": row.status}


@router.post("/heartbeat")
async def heartbeat(
    payload: HeartbeatRequest,
    _auth: None = Depends(verify_bridge_request),
) -> dict[str, Any]:
    return {
        "status": "ok",
        "server_time": datetime.now(timezone.utc).isoformat(),
        "gateway_id": payload.gateway_id,
        "version": payload.version,
        "queue_depth": payload.queue_depth,
    }


@router.post("/events:ingest")
async def ingest_events(
    payload: IngestEventRequest,
    _auth: None = Depends(verify_bridge_request),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await ingest_event(session, payload)


@router.post("/outbox:lease", response_model=LeaseResponse)
async def outbox_lease(
    payload: LeaseRequest,
    _auth: None = Depends(verify_bridge_request),
    session: AsyncSession = Depends(get_session),
) -> LeaseResponse:
    return LeaseResponse(items=await lease_outbox(session, payload))


@router.post("/outbox/{delivery_id}:ack")
async def outbox_ack(
    delivery_id: str,
    payload: AckRequest,
    _auth: None = Depends(verify_bridge_request),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await ack_outbox(session, delivery_id, payload)
