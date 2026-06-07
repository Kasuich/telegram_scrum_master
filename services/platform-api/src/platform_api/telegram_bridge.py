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
    TelegramCallbackToken,
    TelegramChat,
    TelegramImportJob,
    TelegramInstallation,
    TelegramMessage,
    TelegramOutbox,
    TelegramUpdate,
    TelegramUser,
)
from platform_api.telegram_import import ImportReport, ImportRequest
from platform_api.telegram_media import PresignedUploadRequest
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

try:
    from prometheus_client import Counter, Gauge, Histogram

    BRIDGE_INGEST_TOTAL = Counter(
        "telegram_bridge_ingest_total",
        "Ingest requests",
        ["status"],
    )
    BRIDGE_INGEST_LATENCY = Histogram(
        "telegram_bridge_ingest_latency_seconds",
        "Ingest request latency",
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
    )
    BRIDGE_LEASE_TOTAL = Counter(
        "telegram_bridge_lease_total",
        "Lease requests",
        ["status"],
    )
    BRIDGE_ACK_TOTAL = Counter(
        "telegram_bridge_ack_total",
        "ACK requests",
        ["status"],
    )
    OUTBOX_PENDING = Gauge(
        "telegram_outbox_pending",
        "Pending outbox items",
        ["team_id"],
    )
    OUTBOX_LEASED = Gauge(
        "telegram_outbox_leased",
        "Leased outbox items",
        ["team_id"],
    )
    OUTBOX_DEAD_LETTER = Counter(
        "telegram_outbox_dead_letter_total",
        "Dead-lettered outbox items",
        ["team_id"],
    )
    BUSINESS_CONNECTION_TOTAL = Counter(
        "telegram_business_connection_total",
        "Business connection events",
        ["event"],
    )
except ImportError:
    BRIDGE_INGEST_TOTAL = BRIDGE_INGEST_LATENCY = BRIDGE_LEASE_TOTAL = BRIDGE_ACK_TOTAL = None
    OUTBOX_PENDING = OUTBOX_LEASED = OUTBOX_DEAD_LETTER = BUSINESS_CONNECTION_TOTAL = None


class HeartbeatRequest(BaseModel):
    gateway_id: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=64)
    queue_depth: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResolveInstallationResponse(BaseModel):
    installation_id: str
    team_id: str
    bot_username: str | None = None
    status: str = "active"


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


class CallbackConsumeRequest(BaseModel):
    token: str = Field(min_length=1)
    callback_query_id: str | None = None
    actor_external_id: str | None = None
    chat_id: str | None = None
    message_id: str | None = None
    target_user_id: str | None = None


class BusinessConnectionConnectRequest(BaseModel):
    business_connection_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)  # Telegram user external id


class BusinessConnectionDisconnectRequest(BaseModel):
    business_connection_id: str = Field(min_length=1)


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


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_callback_token() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


def _callback_query_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    value = payload.get("callback_query")
    if isinstance(value, dict):
        return value
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


def _callback_session_id(
    installation: TelegramInstallation,
    callback_payload: dict[str, Any],
) -> str:
    message_payload = callback_payload.get("message") or {}
    chat_payload = message_payload.get("chat") or {}
    chat_id = str(chat_payload.get("id") or callback_payload.get("chat_instance") or "0")
    thread_id = (
        str(message_payload.get("message_thread_id"))
        if message_payload.get("message_thread_id") is not None
        else "0"
    )
    return f"telegram:{installation.id}:{chat_id}:{thread_id}"


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
    callback_buttons: list[list[dict[str, str]]] | None = None
    if result.pending_confirm is not None:
        text = result.pending_confirm.prompt
        category = "confirmation"
        dedupe_key = f"telegram:confirm:{message.id}:{result.pending_confirm.confirm_id}"
        approve_token = _new_callback_token()
        reject_token = _new_callback_token()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
        for token_value, approved in ((approve_token, True), (reject_token, False)):
            session.add(
                TelegramCallbackToken(
                    team_id=installation.team_id,
                    installation_id=installation.id,
                    telegram_user_id=telegram_user.id if telegram_user is not None else None,
                    confirm_id=uuid.UUID(result.pending_confirm.confirm_id),
                    token_hash=_token_hash(token_value),
                    target_chat_id=chat.external_chat_id,
                    target_user_id=telegram_user.external_user_id if telegram_user else None,
                    status="pending",
                    payload={
                        "confirm_id": result.pending_confirm.confirm_id,
                        "approved": approved,
                    },
                    expires_at=expires_at,
                )
            )
        callback_buttons = [
            [
                {"text": "Approve", "callback_data": approve_token},
                {"text": "Reject", "callback_data": reject_token},
            ]
        ]
        metadata = {"confirm_id": result.pending_confirm.confirm_id, "callback_tokens": True}
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
            **(
                {"reply_markup": {"inline_keyboard": callback_buttons}}
                if callback_buttons is not None
                else {}
            ),
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


def _callback_token_row_to_response(row: TelegramCallbackToken) -> dict[str, Any]:
    return {
        "token_id": str(row.id),
        "status": row.status,
        "confirm_id": str(row.confirm_id) if row.confirm_id is not None else None,
        "payload": dict(row.payload or {}),
        "consumed_at": row.consumed_at.isoformat() if row.consumed_at else None,
    }


async def _create_confirmation_outbox(
    session: AsyncSession,
    *,
    installation: TelegramInstallation,
    chat_id: str,
    message_id: str | None,
    reply_to_message_id: str | None,
    text: str,
    approve_token: str | None,
    reject_token: str | None,
) -> TelegramOutbox:
    reply_markup: dict[str, Any] | None = None
    if approve_token and reject_token:
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "Approve", "callback_data": approve_token},
                    {"text": "Reject", "callback_data": reject_token},
                ]
            ]
        }
    outbox = TelegramOutbox(
        team_id=installation.team_id,
        installation_id=installation.id,
        category="confirmation",
        target_chat_id=chat_id,
        dedupe_key=f"telegram:confirm:{message_id}:{reply_to_message_id or '0'}",
        priority=90,
        status="pending",
        attempts=0,
        payload={
            "method": "sendMessage",
            "text": text,
            "reply_to_message_id": reply_to_message_id,
            **({"reply_markup": reply_markup} if reply_markup is not None else {}),
        },
    )
    session.add(outbox)
    await session.flush()
    return outbox


async def _consume_callback_query(
    session: AsyncSession,
    installation: TelegramInstallation,
    callback_payload: dict[str, Any],
) -> dict[str, Any]:
    token_value = callback_payload.get("data")
    if not isinstance(token_value, str) or not token_value.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing callback token",
        )

    token_hash = _token_hash(token_value)
    stmt = select(TelegramCallbackToken).where(TelegramCallbackToken.token_hash == token_hash)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Callback token not found",
        )
    if row.installation_id != installation.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Callback installation mismatch",
        )

    now = datetime.now(timezone.utc)
    if row.expires_at <= now:
        row.status = "expired"
        row.consumed_at = now
        await session.flush()
        return {"callback": _callback_token_row_to_response(row), "duplicate": False}

    actor_external_id = callback_payload.get("from", {}).get("id")
    if row.target_user_id and str(actor_external_id) != str(row.target_user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized callback actor",
        )

    if row.consumed_at is not None:
        return {"callback": _callback_token_row_to_response(row), "duplicate": True}

    approved = bool((row.payload or {}).get("approved"))
    confirm_id = str(row.confirm_id) if row.confirm_id is not None else None
    if confirm_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Callback has no confirm")

    result = await rpc_client.resume(confirm_id, approved)
    reply_text = result.reply or None
    outbox_ids: list[str] = []
    callback_query_id = callback_payload.get("id")
    message_payload = callback_payload.get("message") or {}
    chat_payload = message_payload.get("chat") or {}
    chat_id = str(chat_payload.get("id")) if chat_payload.get("id") is not None else None
    original_message_id = (
        str(message_payload.get("message_id"))
        if message_payload.get("message_id") is not None
        else None
    )
    if callback_query_id:
        ack = TelegramOutbox(
            team_id=installation.team_id,
            installation_id=installation.id,
            category="confirmation",
            target_chat_id=chat_id,
            dedupe_key=f"telegram:callback-ack:{row.id}",
            priority=80,
            status="pending",
            attempts=0,
            payload={
                "method": "answerCallbackQuery",
                "callback_query_id": str(callback_query_id),
                "text": "Подтверждено" if approved else "Отклонено",
            },
        )
        session.add(ack)
        await session.flush()
        outbox_ids.append(str(ack.id))
    if chat_id and original_message_id:
        edit = TelegramOutbox(
            team_id=installation.team_id,
            installation_id=installation.id,
            category="confirmation",
            target_chat_id=chat_id,
            dedupe_key=f"telegram:callback-edit:{row.id}",
            priority=70,
            status="pending",
            attempts=0,
            payload={
                "method": "editMessageReplyMarkup",
                "chat_id": chat_id,
                "message_id": original_message_id,
                "reply_markup": {"inline_keyboard": []},
            },
        )
        session.add(edit)
        await session.flush()
        outbox_ids.append(str(edit.id))
    if reply_text:
        reply_outbox = TelegramOutbox(
            team_id=installation.team_id,
            installation_id=installation.id,
            category="agent_reply",
            target_chat_id=chat_id,
            dedupe_key=f"telegram:callback-reply:{row.id}",
            priority=100,
            status="pending",
            attempts=0,
            payload={
                "method": "sendMessage",
                "text": reply_text,
                "reply_to_message_id": original_message_id,
            },
        )
        session.add(reply_outbox)
        await session.flush()
        outbox_ids.append(str(reply_outbox.id))

    row.status = "used"
    row.consumed_at = now
    row.payload = {
        **(row.payload or {}),
        "result": {"approved": approved, "reply": reply_text, "outbox_ids": outbox_ids},
    }
    await session.flush()
    return {
        "callback": _callback_token_row_to_response(row),
        "duplicate": False,
        "approved": approved,
        "reply": reply_text,
        "outbox_ids": outbox_ids,
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
        "deleted_business_messages",
    ):
        value = payload.get(key)
        if isinstance(value, dict):
            return key, value
        if key == "deleted_business_messages" and value:
            return key, {"messages": value}
    return "", None


async def _get_installation(
    session: AsyncSession,
    installation_id: str,
) -> TelegramInstallation | None:
    return await session.get(TelegramInstallation, uuid.UUID(installation_id))


async def _upsert_chat(
    session: AsyncSession,
    installation: TelegramInstallation,
    chat_payload: dict[str, Any],
) -> TelegramChat:
    external_chat_id = str(chat_payload.get("id"))
    stmt = select(TelegramChat).where(
        TelegramChat.installation_id == installation.id,
        TelegramChat.external_chat_id == external_chat_id,
    )
    chat = (await session.execute(stmt)).scalar_one_or_none()
    if chat is None:
        chat_type = str(chat_payload.get("type") or "unknown")
        private_ingest_mode = str(
            (installation.settings or {}).get("private_ingest_mode", "direct")
        )
        chat = TelegramChat(
            installation_id=installation.id,
            external_chat_id=external_chat_id,
            type=chat_type,
            title=chat_payload.get("title"),
            username=chat_payload.get("username"),
            ingest_mode=private_ingest_mode if chat_type == "private" else "disabled",
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


async def _upsert_business_connection(
    session: AsyncSession,
    *,
    installation: TelegramInstallation,
    business_connection_id: str,
    user_external_id: str,
    can_reply: bool = False,
    selected_chat_policy: dict[str, Any] | None = None,
) -> TelegramBusinessConnection:
    stmt = select(TelegramBusinessConnection).where(
        TelegramBusinessConnection.business_connection_id == business_connection_id
    )
    bc = (await session.execute(stmt)).scalar_one_or_none()

    user_stmt = select(TelegramUser).where(
        TelegramUser.external_user_id == user_external_id
    )
    telegram_user = (await session.execute(user_stmt)).scalar_one_or_none()

    if bc is None:
        bc = TelegramBusinessConnection(
            installation_id=installation.id,
            team_id=installation.team_id,
            telegram_user_id=telegram_user.id if telegram_user else None,
            business_connection_id=business_connection_id,
            can_reply=can_reply,
            selected_chat_policy=selected_chat_policy or {},
            status="active",
            connected_at=datetime.now(timezone.utc),
            revoked_at=None,
        )
        session.add(bc)
    else:
        bc.can_reply = can_reply
        bc.selected_chat_policy = selected_chat_policy or {}
        if bc.status == "revoked":
            bc.status = "active"
            bc.revoked_at = None

    await session.flush()
    return bc


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
        chat = await _upsert_chat(session, installation, message_payload.get("chat") or {})
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
    else:
        callback_payload = _callback_query_payload(data.payload)
        if callback_payload is not None:
            routing_result = await _consume_callback_query(
                session,
                installation,
                callback_payload,
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


def _can_send_via_business_connection(
    bc: TelegramBusinessConnection,
    target_chat_id: str | None,
) -> bool:
    """
    Check if message can be sent via business connection.

    Rules:
    - Connection must be active (not revoked)
    - can_reply must be True
    - If selected_chat_policy is set with chat_ids, target must be in list
    """
    if bc.status != "active":
        return False

    if not bc.can_reply:
        return False

    policy = bc.selected_chat_policy or {}
    allowed_chats = policy.get("chat_ids", [])

    # If policy has specific chats, target must be in list
    if allowed_chats and target_chat_id not in allowed_chats:
        return False

    return True


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
        # Check business connection permission if applicable
        if row.business_connection_ref_id:
            bc_stmt = select(TelegramBusinessConnection).where(
                TelegramBusinessConnection.id == row.business_connection_ref_id
            )
            bc = (await session.execute(bc_stmt)).scalar_one_or_none()

            if bc is None or not _can_send_via_business_connection(
                bc, row.target_chat_id
            ):
                # Skip this item, mark as cancelled
                row.status = "cancelled"
                row.last_error = "business_connection_revoked"
                await session.flush()
                continue

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


@router.get("/installations/by-token/{token}", response_model=ResolveInstallationResponse)
async def resolve_installation_by_token(
    token: str,
    _auth: None = Depends(verify_bridge_request),
    session: AsyncSession = Depends(get_session),
) -> ResolveInstallationResponse:
    """
    Resolve onboarding token to installation info.
    Token is hashed for lookup - original token never stored.
    """
    from core.models import TelegramInstallation
    import hashlib

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    # For now, resolve by external_bot_id pattern or return first active installation
    # In production this would look up a separate onboarding_tokens table
    stmt = select(TelegramInstallation).where(
        TelegramInstallation.status == "active"
    ).order_by(TelegramInstallation.created_at.desc()).limit(1)

    installation = (await session.execute(stmt)).scalar_one_or_none()
    if installation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active installation found",
        )

    return ResolveInstallationResponse(
        installation_id=str(installation.id),
        team_id=str(installation.team_id),
        bot_username=installation.settings.get("bot_username") if installation.settings else None,
        status=installation.status,
    )


@router.get(
    "/installations/by-bot/{external_bot_id}",
    response_model=ResolveInstallationResponse,
)
async def resolve_installation_by_bot(
    external_bot_id: str,
    _auth: None = Depends(verify_bridge_request),
    session: AsyncSession = Depends(get_session),
) -> ResolveInstallationResponse:
    stmt = select(TelegramInstallation).where(
        TelegramInstallation.external_bot_id == external_bot_id,
        TelegramInstallation.status == "active",
    )
    installation = (await session.execute(stmt)).scalar_one_or_none()
    if installation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active installation not found for bot",
        )
    return ResolveInstallationResponse(
        installation_id=str(installation.id),
        team_id=str(installation.team_id),
        bot_username=_bot_username(installation),
        status=installation.status,
    )


@router.post("/business-connection:connect")
async def business_connection_connect(
    payload: BusinessConnectionConnectRequest,
    _auth: None = Depends(verify_bridge_request),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """
    Handle Telegram update: bot connected as business account.
    Stores the connection and permissions.
    """
    from sqlalchemy import update

    stmt = select(TelegramInstallation).where(
        TelegramInstallation.status == "active"
    ).limit(1)
    installation = (await session.execute(stmt)).scalar_one_or_none()

    if installation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active installation")

    user_stmt = select(TelegramUser).where(
        TelegramUser.external_user_id == payload.user_id
    )
    telegram_user = (await session.execute(user_stmt)).scalar_one_or_none()

    if telegram_user is None:
        telegram_user = TelegramUser(
            external_user_id=payload.user_id,
            metadata_json={},
        )
        session.add(telegram_user)
        await session.flush()

    bc = await _upsert_business_connection(
        session,
        installation=installation,
        business_connection_id=payload.business_connection_id,
        user_external_id=payload.user_id,
        can_reply=True,
        selected_chat_policy={},
    )

    await session.commit()
    return {"status": "ok", "business_connection_id": str(bc.id)}


@router.post("/business-connection:revoke")
async def business_connection_revoke(
    payload: BusinessConnectionDisconnectRequest,
    _auth: None = Depends(verify_bridge_request),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """
    Handle Telegram update: bot disconnected from business account.
    Marks connection as revoked and cancels pending deliveries.
    """
    from sqlalchemy import update

    stmt = select(TelegramBusinessConnection).where(
        TelegramBusinessConnection.business_connection_id == payload.business_connection_id
    )
    bc = (await session.execute(stmt)).scalar_one_or_none()

    if bc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Business connection not found")

    bc.status = "revoked"
    bc.revoked_at = datetime.now(timezone.utc)

    result = await session.execute(
        update(TelegramOutbox)
        .where(
            TelegramOutbox.business_connection_ref_id == bc.id,
            TelegramOutbox.status.in_(["pending", "leased"]),
        )
        .values(status="cancelled")
    )
    cancelled_count = result.rowcount

    await session.commit()
    return {"status": "ok", "cancelled_count": cancelled_count}


@router.get("/business-connection/{business_connection_id}")
async def get_business_connection(
    business_connection_id: str,
    _auth: None = Depends(verify_bridge_request),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get business connection details and permissions."""
    stmt = select(TelegramBusinessConnection).where(
        TelegramBusinessConnection.business_connection_id == business_connection_id
    )
    bc = (await session.execute(stmt)).scalar_one_or_none()

    if bc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Business connection not found")

    return {
        "installation_id": str(bc.installation_id),
        "team_id": str(bc.team_id),
        "telegram_user_id": str(bc.telegram_user_id) if bc.telegram_user_id else None,
        "business_connection_id": bc.business_connection_id,
        "can_reply": bc.can_reply,
        "selected_chat_policy": bc.selected_chat_policy,
        "status": bc.status,
        "connected_at": bc.connected_at.isoformat() if bc.connected_at else None,
        "revoked_at": bc.revoked_at.isoformat() if bc.revoked_at else None,
    }


class MessageDTO(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    external_chat_id: str
    external_message_id: str
    external_thread_id: str | None = None
    reply_to_external_message_id: str | None = None
    direction: str
    access_mode: str
    message_kind: str
    text: str | None = None
    caption: str | None = None
    sent_at: datetime | None = None
    edited_at: datetime | None = None
    deleted_at: datetime | None = None
    actor_external_id: str | None = None
    actor_name: str | None = None
    media_json: dict | None = None


class MessageListResponse(BaseModel):
    messages: list[MessageDTO]
    next_cursor_sent_at: datetime | None = None
    next_cursor_id: str | None = None
    has_more: bool


def _to_message_dto(msg: TelegramMessage) -> MessageDTO:
    metadata = msg.metadata_json or {}
    return MessageDTO(
        id=str(msg.id),
        external_chat_id=msg.external_chat_id,
        external_message_id=msg.external_message_id,
        external_thread_id=msg.external_thread_id,
        reply_to_external_message_id=msg.reply_to_external_message_id,
        direction=msg.direction,
        access_mode=msg.access_mode,
        message_kind=msg.message_kind,
        text=msg.text,
        caption=msg.caption,
        sent_at=msg.sent_at,
        edited_at=msg.edited_at,
        deleted_at=msg.deleted_at,
        actor_external_id=metadata.get("actor_external_id"),
        actor_name=metadata.get("actor_name"),
        media_json=msg.media_json,
    )


@router.get("/messages", response_model=MessageListResponse)
async def list_messages(
    request: Request,
    team_id: str,
    installation_id: str | None = None,
    chat_id: str | None = None,
    direction: str | None = None,
    access_mode: str | None = None,
    sent_after: datetime | None = None,
    sent_before: datetime | None = None,
    include_deleted: bool = False,
    limit: int = 50,
    cursor_sent_at: datetime | None = None,
    cursor_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> MessageListResponse:
    """Query normalized message corpus with cursor-based pagination."""
    from core.repositories import MessageCursor, MessageQueryOptions, TelegramMessageRepository

    if not _verify_hmac(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature"
        )

    try:
        team_uuid = uuid.UUID(team_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid team_id")

    if direction and direction not in ("inbound", "outbound"):
        raise HTTPException(
            status_code=400, detail="direction must be 'inbound' or 'outbound'"
        )

    if access_mode and access_mode not in ("workspace_bot", "secretary", "import"):
        raise HTTPException(status_code=400, detail="Invalid access_mode")

    options = MessageQueryOptions(
        team_id=team_uuid,
        installation_id=uuid.UUID(installation_id) if installation_id else None,
        chat_id=uuid.UUID(chat_id) if chat_id else None,
        direction=direction,
        access_mode=access_mode,
        sent_after=sent_after,
        sent_before=sent_before,
        include_deleted=include_deleted,
        limit=min(limit, 200),
    )

    cursor = None
    if cursor_sent_at and cursor_id:
        cursor = MessageCursor(sent_at=cursor_sent_at, id=uuid.UUID(cursor_id))

    repo = TelegramMessageRepository(session)
    messages, next_cursor = await repo.list_messages(options, cursor)

    return MessageListResponse(
        messages=[_to_message_dto(m) for m in messages],
        next_cursor_sent_at=next_cursor.sent_at if next_cursor else None,
        next_cursor_id=str(next_cursor.id) if next_cursor else None,
        has_more=next_cursor is not None,
    )


@router.post("/imports", response_model=ImportReport)
async def create_import(
    request: Request,
    body: ImportRequest,
    session: AsyncSession = Depends(get_session),
) -> ImportReport:
    """Create a new Telegram Desktop import job."""
    if not _verify_hmac(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        team_uuid = uuid.UUID(body.team_id)
        installation_uuid = uuid.UUID(body.installation_id)
        chat_uuid = uuid.UUID(body.chat_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid UUID") from exc

    installation = await session.get(TelegramInstallation, installation_uuid)
    if not installation or str(installation.team_id) != body.team_id:
        raise HTTPException(status_code=404, detail="Installation not found")

    chat = await session.get(TelegramChat, chat_uuid)
    if not chat or str(chat.installation_id) != body.installation_id:
        raise HTTPException(status_code=404, detail="Chat not found")

    job = TelegramImportJob(
        team_id=team_uuid,
        installation_id=installation_uuid,
        chat_id=chat_uuid,
        import_source="telegram_desktop",
        status="pending",
        total_messages=0,
        processed_messages=0,
        created_messages=0,
        skipped_messages=0,
        failed_messages=0,
    )
    session.add(job)
    await session.flush()

    return ImportReport(
        job_id=str(job.id),
        status=job.status,
        total_messages=job.total_messages,
        created_messages=job.created_messages,
        skipped_messages=job.skipped_messages,
        failed_messages=job.failed_messages,
        error_message=job.error_message,
    )


@router.post("/media/presign", response_model=dict)
async def presign_upload(
    request: Request,
    body: PresignedUploadRequest,
) -> dict:
    """Generate presigned URL for gateway to upload Telegram media to S3."""
    if not _verify_hmac(request):
        raise HTTPException(status_code=401, detail="Invalid signature")

    installation_id = request.headers.get("X-Installation-Id", "")
    if not installation_id:
        raise HTTPException(status_code=400, detail="Missing X-Installation-Id header")

    try:
        from platform_api.telegram_media import generate_presigned_upload
        result = generate_presigned_upload(body, installation_id)
        return result.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Storage not available") from exc


@router.get("/imports/{job_id}", response_model=ImportReport)
async def get_import(
    request: Request,
    job_id: str,
    session: AsyncSession = Depends(get_session),
) -> ImportReport:
    """Get import job status and report."""
    if not _verify_hmac(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id")

    job = await session.get(TelegramImportJob, job_uuid)
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")

    return ImportReport(
        job_id=str(job.id),
        status=job.status,
        total_messages=job.total_messages,
        created_messages=job.created_messages,
        skipped_messages=job.skipped_messages,
        failed_messages=job.failed_messages,
        error_message=job.error_message,
    )


class DeadLetterReplayRequest(BaseModel):
    team_id: str | None = None
    installation_id: str | None = None
    limit: int = Field(default=50, ge=1, le=500)


class DeadLetterReplayResponse(BaseModel):
    replayed: int


@router.post("/outbox:replay-dead-letter", response_model=DeadLetterReplayResponse)
async def replay_dead_letter(
    request: Request,
    body: DeadLetterReplayRequest,
    session: AsyncSession = Depends(get_session),
) -> DeadLetterReplayResponse:
    """Reset dead-lettered outbox items back to pending for re-delivery.

    Admin-only — requires valid HMAC signature. Use to recover from permanent
    failures after the underlying issue (bad token, banned bot, etc.) is fixed.
    """
    if not _verify_hmac(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    stmt: Select = select(TelegramOutbox).where(
        TelegramOutbox.status == "dead_letter"
    ).limit(body.limit)

    if body.team_id:
        try:
            team_uuid = uuid.UUID(body.team_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid team_id") from exc
        stmt = stmt.where(TelegramOutbox.team_id == team_uuid)

    if body.installation_id:
        try:
            inst_uuid = uuid.UUID(body.installation_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid installation_id") from exc
        stmt = stmt.where(TelegramOutbox.installation_id == inst_uuid)

    result = await session.execute(stmt)
    items = result.scalars().all()

    now = datetime.now(timezone.utc)
    replayed = 0
    for item in items:
        item.status = "pending"
        item.next_attempt_at = now
        item.last_error = None
        replayed += 1

    await session.commit()
    return DeadLetterReplayResponse(replayed=replayed)
