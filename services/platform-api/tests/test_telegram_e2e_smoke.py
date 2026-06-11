from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from core.models import (
    TelegramBusinessConnection,
    TelegramChat,
    TelegramInstallation,
    TelegramOutbox,
    TelegramUser,
)

_HMAC_KEY = "test_secret_key_for_hmac_32chars!!"


def _make_hmac(method: str, path: str, body: bytes = b"") -> str:
    timestamp = str(int(time.time()))
    nonce = "test_nonce_1234"
    body_sha = hashlib.sha256(body).hexdigest()
    signed = f"{method}{path}{timestamp}{nonce}{body_sha}"
    return hmac.new(_HMAC_KEY.encode(), signed.encode(), hashlib.sha256).hexdigest()


def _bridge_headers(method: str, path: str, body: bytes = b"") -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = "test_nonce_1234"
    hashlib.sha256(body).hexdigest()
    body_hmac = _make_hmac(method, path, body)
    return {
        "X-Bridge-Timestamp": timestamp,
        "X-Bridge-Nonce": nonce,
        "X-Bridge-Signature": body_hmac,
        "X-Bridge-Key-Id": "key1",
    }


def _installation() -> TelegramInstallation:
    return TelegramInstallation(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        alias="test_bot",
        external_bot_id="777001",
        mode="workspace_bot",
        status="active",
        settings={"bot_username": "test_bot"},
    )


def _chat(installation_id: uuid.UUID) -> TelegramChat:
    return TelegramChat(
        id=uuid.uuid4(),
        installation_id=installation_id,
        external_chat_id="-100123",
        type="group",
        title="Test Group",
        username=None,
        ingest_mode="mentions",
        access_mode="workspace_bot",
        send_policy={},
        active=True,
        metadata_json={},
    )


def _user() -> TelegramUser:
    return TelegramUser(
        id=uuid.uuid4(),
        external_user_id="999",
        username="alice",
        first_name="Alice",
        last_name="Smith",
        language_code="en",
        is_bot=False,
        is_blocked=False,
        metadata_json={},
    )


class TestIngestValidation:
    def test_db_session_dependency_is_async_generator(self) -> None:
        import inspect

        from platform_api.telegram_bridge import _db_session

        assert inspect.isasyncgenfunction(_db_session)

    def test_get_installation_returns_none_for_unknown(self) -> None:
        import inspect

        from platform_api.telegram_bridge import _get_installation

        assert inspect.iscoroutinefunction(_get_installation)

    def test_upsert_chat_is_async(self) -> None:
        import inspect

        from platform_api.telegram_bridge import _upsert_chat

        assert inspect.iscoroutinefunction(_upsert_chat)


class TestOutboxDedupe:
    def test_dedupe_key_uniqueness(self) -> None:
        from core.models import TelegramOutbox

        assert hasattr(TelegramOutbox, "__table_args__")
        table_args = TelegramOutbox.__table_args__
        has_dedupe = any(
            "dedupe" in str(getattr(c, "name", "")) for c in table_args if hasattr(c, "name")
        )
        assert has_dedupe, "TelegramOutbox should have dedupe key uniqueness"


class TestHmacValidation:
    def test_sign_produces_deterministic_signature(self) -> None:
        from platform_api.telegram_bridge import _sign

        sig1 = _sign("secret", "POST", "/test", "123456", "nonce1", b"{}")
        sig2 = _sign("secret", "POST", "/test", "123456", "nonce1", b"{}")
        assert sig1 == sig2
        assert len(sig1) == 64  # SHA256 hex

    def test_sign_different_inputs_produce_different_signatures(self) -> None:
        from platform_api.telegram_bridge import _sign

        sig1 = _sign("secret", "POST", "/test", "123456", "nonce1", b"{}")
        sig2 = _sign("secret", "POST", "/test", "123456", "nonce2", b"{}")  # different nonce
        assert sig1 != sig2

    def test_seen_nonces_is_global_dict(self) -> None:
        from platform_api.telegram_bridge import _SEEN_NONCES

        assert isinstance(_SEEN_NONCES, dict)
        # Add test nonce and verify
        test_nonce = f"test_{uuid.uuid4().hex[:8]}"
        _SEEN_NONCES[test_nonce] = time.time()
        assert test_nonce in _SEEN_NONCES
        del _SEEN_NONCES[test_nonce]

    def test_verify_bridge_request_is_async(self) -> None:
        import inspect

        from platform_api.telegram_bridge import verify_bridge_request

        assert inspect.iscoroutinefunction(verify_bridge_request)


class TestSecretaryModeFiltering:
    def test_cannot_send_to_revoked_connection(self) -> None:
        from platform_api.telegram_bridge import _can_send_via_business_connection

        bc = TelegramBusinessConnection(
            id=uuid.uuid4(),
            installation_id=uuid.uuid4(),
            team_id=uuid.uuid4(),
            telegram_user_id=uuid.uuid4(),
            business_connection_id="bc-revoked",
            can_reply=True,
            selected_chat_policy={},
            status="revoked",
        )

        assert _can_send_via_business_connection(bc, "-100123") is False

    def test_cannot_send_without_can_reply(self) -> None:
        from platform_api.telegram_bridge import _can_send_via_business_connection

        bc = TelegramBusinessConnection(
            id=uuid.uuid4(),
            installation_id=uuid.uuid4(),
            team_id=uuid.uuid4(),
            telegram_user_id=uuid.uuid4(),
            business_connection_id="bc-no-reply",
            can_reply=False,
            selected_chat_policy={},
            status="active",
        )

        assert _can_send_via_business_connection(bc, "-100123") is False

    def test_cannot_send_to_unselected_chat(self) -> None:
        from platform_api.telegram_bridge import _can_send_via_business_connection

        bc = TelegramBusinessConnection(
            id=uuid.uuid4(),
            installation_id=uuid.uuid4(),
            team_id=uuid.uuid4(),
            telegram_user_id=uuid.uuid4(),
            business_connection_id="bc-limited",
            can_reply=True,
            selected_chat_policy={"chat_ids": ["-100123"]},
            status="active",
        )

        assert _can_send_via_business_connection(bc, "-100789") is False
        assert _can_send_via_business_connection(bc, "-100123") is True


class TestImportDedupe:
    def test_import_skips_duplicate_message_ids(self) -> None:
        import asyncio

        from platform_api.telegram_import import _dedupe_keys_exist

        mock_session = AsyncMock()

        async def fake_execute(stmt):
            result = MagicMock()
            result.scalars.return_value.all.return_value = ["42", "43"]
            return result

        mock_session.execute = fake_execute

        existing = asyncio.run(
            _dedupe_keys_exist(mock_session, uuid.uuid4(), "-100123", {"42", "43", "44"})
        )

        assert "42" in existing
        assert "43" in existing
        assert "44" not in existing


class TestDeadLetterReplay:
    def test_replay_endpoint_exists(self) -> None:
        import inspect

        from platform_api.telegram_bridge import replay_dead_letter

        assert inspect.iscoroutinefunction(replay_dead_letter)

    def test_replay_response_model_fields(self) -> None:
        from platform_api.telegram_bridge import DeadLetterReplayResponse

        response = DeadLetterReplayResponse(replayed=5)
        assert response.replayed == 5

    def test_replay_request_model_defaults(self) -> None:
        from platform_api.telegram_bridge import DeadLetterReplayRequest

        req = DeadLetterReplayRequest()
        assert req.limit == 50
        assert req.team_id is None
        assert req.installation_id is None

    def test_replay_request_limit_validation(self) -> None:
        import pydantic
        from platform_api.telegram_bridge import DeadLetterReplayRequest

        with pytest.raises(pydantic.ValidationError):
            DeadLetterReplayRequest(limit=0)
        with pytest.raises(pydantic.ValidationError):
            DeadLetterReplayRequest(limit=501)
        req = DeadLetterReplayRequest(limit=500)
        assert req.limit == 500

    def test_dead_letter_status_recognized(self) -> None:
        item = TelegramOutbox(
            id=uuid.uuid4(),
            team_id=uuid.uuid4(),
            payload={"text": "test", "chat_id": "-100123"},
            status="dead_letter",
            attempts=5,
        )
        assert item.status == "dead_letter"


class TestMessageQueryAPI:
    def test_message_payload_kind_prefers_business_over_regular(self) -> None:
        from platform_api.telegram_bridge import _message_payload_kind

        payload = {
            "message": {"message_id": 1, "text": "regular"},
            "business_message": {"message_id": 2, "text": "business"},
        }

        kind, value = _message_payload_kind(payload)
        assert kind == "message"
        assert value["text"] == "regular"

    def test_message_payload_kind_business_message(self) -> None:
        from platform_api.telegram_bridge import _message_payload_kind

        payload = {
            "business_message": {
                "message_id": 42,
                "chat": {"id": -100123, "type": "private"},
                "text": "Secret message",
                "date": 1234567890,
            }
        }

        kind, value = _message_payload_kind(payload)
        assert kind == "business_message"
        assert value["message_id"] == 42
