from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from core.models import (
    TelegramCallbackToken,
    TelegramChat,
    TelegramInstallation,
    TelegramMessage,
    TelegramOnboardingSession,
    TelegramUser,
)
from core.react import AgentResult, PendingConfirm
from fastapi import HTTPException
from platform_api.telegram_bridge import _consume_callback_query, _enqueue_agent_result


class _FakeResult:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def scalar_one_or_none(self) -> object | None:
        return self._row


class _FakeSession:
    def __init__(
        self,
        row: object | None = None,
        get_rows: dict[type, object] | None = None,
    ) -> None:
        self.row = row
        self.get_rows = get_rows or {}
        self.added: list[object] = []
        self.flush_calls = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_calls += 1

    async def execute(self, _stmt: object) -> _FakeResult:
        return _FakeResult(self.row)

    async def get(self, model: type, _row_id: object) -> object | None:
        return self.get_rows.get(model)


def _installation() -> TelegramInstallation:
    return TelegramInstallation(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        alias="pm_bot",
        external_bot_id="777000",
        mode="workspace_bot",
        status="active",
        settings={"bot_username": "pm_bot"},
    )


def _chat() -> TelegramChat:
    return TelegramChat(
        id=uuid.uuid4(),
        installation_id=uuid.uuid4(),
        external_chat_id="-100123",
        type="group",
        title="Team",
        username=None,
        ingest_mode="mentions",
        access_mode="workspace_bot",
        send_policy={},
        active=True,
        metadata_json={},
    )


def _message() -> TelegramMessage:
    return TelegramMessage(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        installation_id=uuid.uuid4(),
        chat_id=uuid.uuid4(),
        telegram_user_id=uuid.uuid4(),
        business_connection_ref_id=None,
        raw_update_id=uuid.uuid4(),
        direction="inbound",
        access_mode="workspace_bot",
        external_chat_id="-100123",
        external_message_id="42",
        external_thread_id="7",
        reply_to_external_message_id=None,
        message_kind="message",
        import_source=None,
        text="status?",
        caption=None,
        sent_at=None,
        edited_at=None,
        deleted_at=None,
        media_json={},
        metadata_json={},
    )


def _telegram_user() -> TelegramUser:
    return TelegramUser(
        id=uuid.uuid4(),
        external_user_id="991",
        username="ivan",
        first_name="Ivan",
        last_name="Petrov",
        language_code="ru",
        is_bot=False,
        is_blocked=False,
        metadata_json={},
    )


def _callback_token_row(
    *,
    approved: bool,
    installation_id: uuid.UUID,
    consumed_at: datetime | None = None,
    expires_at: datetime | None = None,
    target_user_id: str | None = "991",
) -> TelegramCallbackToken:
    now = datetime.now(tz=timezone.utc)
    return TelegramCallbackToken(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        installation_id=installation_id,
        telegram_user_id=uuid.uuid4(),
        confirm_id=uuid.uuid4(),
        token_hash="token-hash",
        target_chat_id="-100123",
        target_user_id=target_user_id,
        status="used" if consumed_at else "pending",
        payload={"approved": approved},
        expires_at=expires_at or (now + timedelta(minutes=15)),
        consumed_at=consumed_at,
    )


@pytest.mark.asyncio
async def test_enqueue_pending_confirm_creates_callback_tokens_and_buttons() -> None:
    session = _FakeSession()
    installation = _installation()
    chat = _chat()
    message = _message()
    telegram_user = _telegram_user()
    result = AgentResult(
        pending_confirm=PendingConfirm(
            confirm_id=str(uuid.uuid4()),
            tool_name="tracker_create_issue",
            tool_args={"summary": "Bug"},
            risk="medium",
            prompt="Approve?",
        ),
        session_id="s1",
    )

    outbox = await _enqueue_agent_result(
        session,
        installation=installation,
        chat=chat,
        message=message,
        telegram_user=telegram_user,
        result=result,
    )

    assert outbox is not None
    assert outbox.payload["reply_markup"]["inline_keyboard"][0][0]["text"] == "Approve"
    assert outbox.payload["reply_markup"]["inline_keyboard"][0][1]["text"] == "Reject"
    approve_token = outbox.payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    reject_token = outbox.payload["reply_markup"]["inline_keyboard"][0][1]["callback_data"]
    assert approve_token != reject_token
    assert len(session.added) == 3
    token_rows = [obj for obj in session.added if isinstance(obj, TelegramCallbackToken)]
    assert len(token_rows) == 2
    assert {row.payload["approved"] for row in token_rows} == {True, False}
    assert all(len(row.token_hash) == 64 for row in token_rows)


@pytest.mark.asyncio
async def test_consume_callback_token_resumes_once_and_creates_outbox() -> None:
    installation = _installation()
    row = _callback_token_row(approved=True, installation_id=installation.id)
    session = _FakeSession(row=row)
    callback_payload = {
        "id": "cbq-1",
        "data": "token-value",
        "from": {"id": 991},
        "message": {
            "message_id": 7,
            "chat": {"id": -100123},
            "message_thread_id": 7,
        },
    }

    with (
        patch("platform_api.telegram_bridge._token_hash", return_value=row.token_hash),
        patch(
            "platform_api.telegram_bridge.rpc_client.resume",
            AsyncMock(return_value=AgentResult(reply="Done", session_id="s1")),
        ) as resume,
    ):
        result = await _consume_callback_query(session, installation, callback_payload)

    assert result["approved"] is True
    assert result["duplicate"] is False
    assert result["reply"] == "Done"
    resume.assert_awaited_once_with(str(row.confirm_id), True)
    assert row.status == "used"
    assert row.consumed_at is not None
    assert len(result["outbox_ids"]) == 3
    methods = [getattr(obj, "payload", {}).get("method") for obj in session.added]
    assert methods == ["answerCallbackQuery", "editMessageReplyMarkup", "sendMessage"]


@pytest.mark.asyncio
async def test_consume_onboarding_identity_button_advances_flow() -> None:
    installation = _installation()
    telegram_user = _telegram_user()
    onboarding = TelegramOnboardingSession(
        id=uuid.uuid4(),
        team_id=installation.team_id,
        installation_id=installation.id,
        telegram_user_id=telegram_user.id,
        status="pending",
        step_key="confirm_tracker",
        answers_json={"tracker_login": "ivan.petrov"},
        attempts=1,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    row = TelegramCallbackToken(
        id=uuid.uuid4(),
        team_id=installation.team_id,
        installation_id=installation.id,
        telegram_user_id=telegram_user.id,
        confirm_id=None,
        token_hash="token-hash",
        target_chat_id="991",
        target_user_id="991",
        status="pending",
        payload={
            "action": "onboarding_identity",
            "onboarding_id": str(onboarding.id),
            "approved": True,
        },
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    session = _FakeSession(
        row=row,
        get_rows={
            TelegramUser: telegram_user,
            TelegramOnboardingSession: onboarding,
        },
    )
    callback_payload = {
        "id": "cbq-auth-1",
        "data": "token-value",
        "from": {"id": 991},
        "message": {"message_id": 7, "chat": {"id": 991}},
    }

    with patch("platform_api.telegram_bridge._token_hash", return_value=row.token_hash):
        result = await _consume_callback_query(session, installation, callback_payload)

    assert result["authorization"] == "tracker_confirmed"
    assert onboarding.step_key == "default_board"
    assert row.status == "used"
    methods = [getattr(obj, "payload", {}).get("method") for obj in session.added]
    assert methods == ["sendMessage", "answerCallbackQuery", "editMessageReplyMarkup"]


@pytest.mark.asyncio
async def test_consume_callback_token_duplicate_skips_resume() -> None:
    installation = _installation()
    row = _callback_token_row(
        approved=False,
        installation_id=installation.id,
        consumed_at=datetime.now(timezone.utc),
    )
    session = _FakeSession(row=row)
    callback_payload = {
        "id": "cbq-1",
        "data": "token-value",
        "from": {"id": 991},
        "message": {"message_id": 7, "chat": {"id": -100123}},
    }

    with (
        patch("platform_api.telegram_bridge._token_hash", return_value=row.token_hash),
        patch("platform_api.telegram_bridge.rpc_client.resume", AsyncMock()) as resume,
    ):
        result = await _consume_callback_query(session, installation, callback_payload)

    assert result["duplicate"] is True
    resume.assert_not_awaited()


@pytest.mark.asyncio
async def test_consume_callback_token_rejects_wrong_actor() -> None:
    installation = _installation()
    row = _callback_token_row(approved=True, installation_id=installation.id)
    session = _FakeSession(row=row)
    callback_payload = {
        "id": "cbq-1",
        "data": "token-value",
        "from": {"id": 123},
        "message": {"message_id": 7, "chat": {"id": -100123}},
    }

    with patch("platform_api.telegram_bridge._token_hash", return_value=row.token_hash):
        with pytest.raises(HTTPException) as exc:
            await _consume_callback_query(session, installation, callback_payload)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_consume_callback_token_expires_without_resume() -> None:
    installation = _installation()
    row = _callback_token_row(
        approved=True,
        installation_id=installation.id,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    session = _FakeSession(row=row)
    callback_payload = {
        "id": "cbq-1",
        "data": "token-value",
        "from": {"id": 991},
        "message": {"message_id": 7, "chat": {"id": -100123}},
    }

    with (
        patch("platform_api.telegram_bridge._token_hash", return_value=row.token_hash),
        patch("platform_api.telegram_bridge.rpc_client.resume", AsyncMock()) as resume,
    ):
        result = await _consume_callback_query(session, installation, callback_payload)

    assert result["callback"]["status"] == "expired"
    resume.assert_not_awaited()
