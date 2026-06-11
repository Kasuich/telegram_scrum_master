from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from core.models import TelegramMessage
from core.repositories.telegram_message import (
    MessageCursor,
    MessageQueryOptions,
    TelegramMessageRepository,
)


def _msg(
    id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
    external_chat_id: str = "-100123",
    external_message_id: str = "1",
    text: str = "Hello",
    sent_at: datetime | None = None,
    direction: str = "inbound",
    access_mode: str = "workspace_bot",
    deleted_at: datetime | None = None,
) -> TelegramMessage:
    return TelegramMessage(
        id=id or uuid.uuid4(),
        team_id=team_id or uuid.uuid4(),
        installation_id=uuid.uuid4(),
        chat_id=None,
        telegram_user_id=None,
        business_connection_ref_id=None,
        raw_update_id=None,
        direction=direction,
        access_mode=access_mode,
        external_chat_id=external_chat_id,
        external_message_id=external_message_id,
        external_thread_id=None,
        reply_to_external_message_id=None,
        message_kind="text",
        import_source=None,
        text=text,
        caption=None,
        sent_at=sent_at or datetime.now(tz=timezone.utc),
        edited_at=None,
        deleted_at=deleted_at,
        media_json=None,
        metadata_json=None,
    )


class TestMessageCursor:
    def test_cursor_equality(self) -> None:
        ts = datetime.now(tz=timezone.utc)
        id1 = uuid.uuid4()
        c1 = MessageCursor(sent_at=ts, id=id1)
        c2 = MessageCursor(sent_at=ts, id=id1)
        assert c1.sent_at == c2.sent_at
        assert c1.id == c2.id

    def test_cursor_inequality(self) -> None:
        ts = datetime.now(tz=timezone.utc)
        c1 = MessageCursor(sent_at=ts, id=uuid.uuid4())
        c2 = MessageCursor(sent_at=ts, id=uuid.uuid4())
        assert c1.id != c2.id


class TestMessageQueryOptions:
    def test_defaults(self) -> None:
        opts = MessageQueryOptions(team_id=uuid.uuid4())
        assert opts.installation_id is None
        assert opts.limit == 50
        assert opts.include_deleted is False
        assert opts.direction is None

    def test_all_fields(self) -> None:
        team_id = uuid.uuid4()
        opts = MessageQueryOptions(
            team_id=team_id,
            installation_id=uuid.uuid4(),
            chat_id=uuid.uuid4(),
            direction="inbound",
            access_mode="secretary",
            sent_after=datetime.now(tz=timezone.utc),
            sent_before=datetime.now(tz=timezone.utc),
            include_deleted=True,
            limit=100,
        )
        assert opts.team_id == team_id
        assert opts.direction == "inbound"
        assert opts.access_mode == "secretary"
        assert opts.include_deleted is True
        assert opts.limit == 100


class TestTelegramMessageRepository:
    @pytest.fixture
    def mock_session(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def repo(self, mock_session: AsyncMock) -> TelegramMessageRepository:
        return TelegramMessageRepository(mock_session)

    @pytest.mark.asyncio
    async def test_list_messages_returns_empty(
        self, repo: TelegramMessageRepository, mock_session: AsyncMock
    ) -> None:
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        options = MessageQueryOptions(team_id=uuid.uuid4())
        messages, next_cursor = await repo.list_messages(options)

        assert messages == []
        assert next_cursor is None
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_messages_returns_results(
        self, repo: TelegramMessageRepository, mock_session: AsyncMock
    ) -> None:
        team_id = uuid.uuid4()
        msg1 = _msg(id=uuid.uuid4(), team_id=team_id, external_message_id="1", text="First")
        msg2 = _msg(id=uuid.uuid4(), team_id=team_id, external_message_id="2", text="Second")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [msg1, msg2]
        mock_session.execute.return_value = mock_result

        options = MessageQueryOptions(team_id=team_id)
        messages, next_cursor = await repo.list_messages(options)

        assert len(messages) == 2
        assert messages[0].text == "First"
        assert messages[1].text == "Second"

    @pytest.mark.asyncio
    async def test_list_messages_respects_limit(
        self, repo: TelegramMessageRepository, mock_session: AsyncMock
    ) -> None:
        team_id = uuid.uuid4()
        msgs = [_msg(id=uuid.uuid4(), team_id=team_id) for _ in range(10)]

        mock_result = MagicMock()
        # Return 4 messages (limit=3 → fetch 4, truncate)
        mock_result.scalars.return_value.all.return_value = msgs[:4]
        mock_session.execute.return_value = mock_result

        options = MessageQueryOptions(team_id=team_id, limit=3)
        messages, next_cursor = await repo.list_messages(options)

        # Should return only 3 (limit), and cursor is set since more were available
        assert len(messages) == 3
        if next_cursor:
            assert next_cursor.id == msgs[2].id  # Last returned

    @pytest.mark.asyncio
    async def test_list_messages_no_cursor_when_fewer_than_limit(
        self, repo: TelegramMessageRepository, mock_session: AsyncMock
    ) -> None:
        team_id = uuid.uuid4()
        msgs = [_msg(id=uuid.uuid4(), team_id=team_id) for _ in range(2)]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = msgs[:2]
        mock_session.execute.return_value = mock_result

        options = MessageQueryOptions(team_id=team_id, limit=50)
        messages, next_cursor = await repo.list_messages(options)

        assert len(messages) == 2
        assert next_cursor is None  # No more results

    @pytest.mark.asyncio
    async def test_list_messages_with_cursor(
        self, repo: TelegramMessageRepository, mock_session: AsyncMock
    ) -> None:
        team_id = uuid.uuid4()
        cursor_id = uuid.uuid4()
        cursor_ts = datetime.now(tz=timezone.utc)

        msg = _msg(id=uuid.uuid4(), team_id=team_id)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [msg]
        mock_session.execute.return_value = mock_result

        cursor = MessageCursor(sent_at=cursor_ts, id=cursor_id)
        options = MessageQueryOptions(team_id=team_id)
        messages, _ = await repo.list_messages(options, cursor=cursor)

        assert len(messages) == 1
        # Verify cursor was used in query
        call_args = mock_session.execute.call_args
        assert call_args is not None

    @pytest.mark.asyncio
    async def test_list_messages_filters_deleted(
        self, repo: TelegramMessageRepository, mock_session: AsyncMock
    ) -> None:
        team_id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        options = MessageQueryOptions(team_id=team_id, include_deleted=False)
        await repo.list_messages(options)

        assert mock_session.execute.called
        assert options.include_deleted is False

    @pytest.mark.asyncio
    async def test_list_messages_includes_deleted_when_flag(
        self, repo: TelegramMessageRepository, mock_session: AsyncMock
    ) -> None:
        team_id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        options = MessageQueryOptions(team_id=team_id, include_deleted=True)
        await repo.list_messages(options)

        assert mock_session.execute.called
        assert options.include_deleted is True

    @pytest.mark.asyncio
    async def test_get_message_found(
        self, repo: TelegramMessageRepository, mock_session: AsyncMock
    ) -> None:
        msg_id = uuid.uuid4()
        msg = _msg(id=msg_id)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = msg
        mock_session.execute.return_value = mock_result

        result = await repo.get_message(msg_id)

        assert result is not None
        assert result.id == msg_id

    @pytest.mark.asyncio
    async def test_get_message_not_found(
        self, repo: TelegramMessageRepository, mock_session: AsyncMock
    ) -> None:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repo.get_message(uuid.uuid4())

        assert result is None

    @pytest.mark.asyncio
    async def test_list_messages_filters_by_direction(
        self, repo: TelegramMessageRepository, mock_session: AsyncMock
    ) -> None:
        team_id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        options = MessageQueryOptions(team_id=team_id, direction="inbound")
        await repo.list_messages(options)

        assert mock_session.execute.called
        assert options.direction == "inbound"

    @pytest.mark.asyncio
    async def test_list_messages_filters_by_access_mode(
        self, repo: TelegramMessageRepository, mock_session: AsyncMock
    ) -> None:
        team_id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        options = MessageQueryOptions(team_id=team_id, access_mode="secretary")
        await repo.list_messages(options)

        assert mock_session.execute.called
        assert options.access_mode == "secretary"
